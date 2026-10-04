"""Bluetooth for HA: the webhook a tablet posts its advertisements to.

One webhook per Dashie config entry, ``local_only``. The webhook id is the credential,
so no HA token ever lives on the tablet. The tablet learns the id from a command this
integration sends it (``setHaBleWebhook``), and only when the tablet's device info says
it can use one (``haBle.supported``), so older apps never see an unknown command. The
tablet reports only ``haBle.webhookHash`` (``webhook_hash``), never the id itself: its
device info is readable by anyone who can reach port 2323.

Request (tablet → HA), JSON::

    {"v": 1, "enabled": true, "screenOn": false, "configHash": "…",
     "adverts": [ <see ble_scanner._parse_advert> ]}

Response: ``{"status": "ok", "accepted": n, "configHash": "…"}``, plus ``"config"``
(``ble_matchers``) whenever the tablet's ``configHash`` is stale.

``enabled: false`` unregisters the tablet's scanner. A tablet that never posts never
gets a scanner, so HA's Bluetooth page lists only tablets that have the feature on.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .ble_matchers import async_build_scan_config
from .const import CONF_DEVICE_ID, DOMAIN
from .coordinator import DashieCoordinator

_LOGGER = logging.getLogger(__name__)

CONF_BLE_WEBHOOK_ID = "ble_webhook_id"
CMD_SET_WEBHOOK = "setHaBleWebhook"
_CONFIG_TTL = 60  # seconds a built scan config is reused
_HANDOFF_RETRY = 300  # seconds between hand-off attempts to a tablet that has not taken it


def webhook_hash(webhook_id: str) -> str:
    """First 16 hex of sha256: what the tablet reports (HaBleApiHandler.webhookHash)."""
    return hashlib.sha256(webhook_id.encode()).hexdigest()[:16]


class DashieBle:
    """Bluetooth-for-HA state for one tablet (one config entry)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: DashieCoordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.webhook_id: str = entry.data[CONF_BLE_WEBHOOK_ID]
        self._scanner = None
        self._unload_scanner: CALLBACK_TYPE | None = None
        self._config: dict[str, Any] | None = None
        self._config_hash = ""
        self._config_at = 0.0
        self._handoff_at = 0.0
        self._warned_no_bluetooth = False

    # --- hand-off: tell the tablet where to post -------------------------------------

    @callback
    def async_on_coordinator_update(self) -> None:
        """After each poll: hand the webhook to a tablet that can use it and lacks it."""
        info = (self.coordinator.data or {}).get("haBle")
        if not isinstance(info, dict) or not info.get("supported"):
            return
        if info.get("webhookHash") == webhook_hash(self.webhook_id):
            return
        now = time.monotonic()
        if now - self._handoff_at < _HANDOFF_RETRY:
            return
        self._handoff_at = now
        self.hass.async_create_task(self._async_hand_off())

    async def _async_hand_off(self) -> None:
        params = {"webhookId": self.webhook_id, "path": webhook.async_generate_path(self.webhook_id)}
        try:
            params["url"] = get_url(self.hass, allow_external=False, allow_cloud=False)
        except NoURLAvailableError:
            pass  # the tablet falls back to the HA address it already has
        result = await self.coordinator.async_send(CMD_SET_WEBHOOK, **params)
        _LOGGER.info(
            "SMARTHOME_BLE_HANDOFF %s ok=%s", self.entry.title, result.ok
        )

    # --- the webhook -----------------------------------------------------------------

    async def async_handle(self, hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"status": "error", "message": "invalid JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"status": "error", "message": "object expected"}, status=400)

        if body.get("enabled") is False:
            self.async_stop_scanner()
            return web.json_response({"status": "ok", "accepted": 0})

        if "bluetooth" not in hass.config.components:
            if not self._warned_no_bluetooth:
                _LOGGER.warning(
                    "DROP: %s is sending Bluetooth adverts but HA's Bluetooth integration is not loaded",
                    self.entry.title,
                )
                self._warned_no_bluetooth = True
            return web.json_response({"status": "no_bluetooth", "accepted": 0})

        if self._scanner is None:
            self._async_start_scanner()
        adverts = body.get("adverts") or []
        if not isinstance(adverts, list):
            adverts = []
        accepted, dropped = self._scanner.async_on_batch(adverts)
        _LOGGER.debug(
            "SMARTHOME_BLE_BATCH %s accepted=%d dropped=%d screenOn=%s",
            self.entry.title, accepted, dropped, body.get("screenOn"),
        )

        reply: dict[str, Any] = {"status": "ok", "accepted": accepted}
        config, config_hash = await self._async_scan_config()
        reply["heardVia"] = self._heard_via(config["addresses"])
        reply["configHash"] = config_hash
        if body.get("configHash") != config_hash:
            reply["config"] = config
        return web.json_response(reply)

    async def _async_scan_config(self) -> tuple[dict[str, Any], str]:
        now = time.monotonic()
        if self._config is None or now - self._config_at > _CONFIG_TTL:
            self._config = await async_build_scan_config(self.hass)
            blob = json.dumps(self._config, sort_keys=True, default=str).encode()
            self._config_hash = hashlib.sha256(blob).hexdigest()[:16]
            self._config_at = now
        return self._config, self._config_hash

    @callback
    def _heard_via(self, addresses: list[str]) -> dict[str, str]:
        """For each of HA's Bluetooth devices HA currently hears: is it through this tablet or another receiver?

        HA keeps the best receiver per device, so "this" means the tablet is what HA is using for it. The tablet
        shows it on its Bluetooth page; it cannot know about HA's other receivers on its own.
        """
        from homeassistant.components import bluetooth  # loaded: checked before the scanner started

        source = self._scanner.source if self._scanner is not None else None
        via: dict[str, str] = {}
        for address in addresses:
            info = bluetooth.async_last_service_info(self.hass, address, connectable=False)
            if info is not None:
                via[address] = "this" if info.source == source else "other"
        return via

    # --- the scanner -----------------------------------------------------------------

    @callback
    def _async_start_scanner(self) -> None:
        from .ble_scanner import async_start_scanner  # needs the bluetooth integration

        device_id = self.entry.data[CONF_DEVICE_ID]
        ha_device = dr.async_get(self.hass).async_get_device(identifiers={(DOMAIN, device_id)})
        self._scanner, self._unload_scanner = async_start_scanner(
            self.hass, self.entry, device_id, ha_device.id if ha_device else None
        )

    @callback
    def async_stop_scanner(self) -> None:
        if self._unload_scanner is not None:
            self._unload_scanner()
        self._scanner = None
        self._unload_scanner = None

    @callback
    def async_unload(self) -> None:
        webhook.async_unregister(self.hass, self.webhook_id)
        self.async_stop_scanner()


@callback
def async_setup_ble(hass: HomeAssistant, entry: ConfigEntry, coordinator: DashieCoordinator) -> None:
    """Give the entry its webhook and listen for tablets that can use it."""
    if CONF_BLE_WEBHOOK_ID not in entry.data:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_BLE_WEBHOOK_ID: webhook.async_generate_id()}
        )
    ble = DashieBle(hass, entry, coordinator)
    webhook.async_register(
        hass, DOMAIN, f"Dashie Bluetooth ({entry.title})", ble.webhook_id, ble.async_handle,
        local_only=True, allowed_methods=["POST"],
    )
    entry.async_on_unload(coordinator.async_add_listener(ble.async_on_coordinator_update))
    entry.async_on_unload(ble.async_unload)
