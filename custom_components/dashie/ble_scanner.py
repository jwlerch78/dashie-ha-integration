"""A Dashie tablet as a Home Assistant remote Bluetooth scanner.

The tablet listens (passively) and posts what it hears to this integration's webhook
(``ble_webhook.py``); each batch is fed into HA's Bluetooth manager here, the way
Shelly feeds its devices' scan results. HA's own integrations (Inkbird, Govee,
SwitchBot, ...) then discover and update sensors heard by the tablet, with the tablet
shown as their source.

Only imported once the ``bluetooth`` integration is loaded (it is an
``after_dependency``, never a hard one: a box whose Bluetooth stack fails to start
must still load every other Dashie feature).
"""
from __future__ import annotations

import hashlib
import inspect
import logging
from typing import Any

from homeassistant.components.bluetooth import (
    MONOTONIC_TIME,
    BaseHaRemoteScanner,
    async_register_scanner,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

# One batch is at most a few seconds of adverts (~3k/min measured in a busy house).
MAX_ADVERTS_PER_BATCH = 2000

# `source_*` arguments arrived after 2025.1, the oldest HA this integration supports.
_REGISTER_TAKES_SOURCE = (
    "source_config_entry_id" in inspect.signature(async_register_scanner).parameters
)


def scanner_source(device_id: str) -> str:
    """A stable MAC-shaped id for the tablet's scanner.

    Android does not let apps read their own Bluetooth address, so derive one from the
    Dashie device id, marked locally administered so it can never collide with a
    real radio.
    """
    raw = bytearray(hashlib.sha256(device_id.encode()).digest()[:6])
    raw[0] = (raw[0] | 0x02) & 0xFE
    return ":".join(f"{b:02X}" for b in raw)


class DashieRemoteScanner(BaseHaRemoteScanner):
    """Advertisements heard by one tablet."""

    @callback
    def async_on_batch(self, adverts: list[Any]) -> tuple[int, int]:
        """Feed one batch to HA. Returns (accepted, dropped)."""
        now = MONOTONIC_TIME()
        accepted = dropped = 0
        for item in adverts[:MAX_ADVERTS_PER_BATCH]:
            parsed = _parse_advert(item)
            if parsed is None:
                dropped += 1
                continue
            address, rssi, name, uuids, service_data, mfr_data, tx_power, age = parsed
            self._async_on_advertisement(
                address, rssi, name, uuids, service_data, mfr_data, tx_power, {},
                now - age,
            )
            accepted += 1
        dropped += max(0, len(adverts) - MAX_ADVERTS_PER_BATCH)
        if dropped:
            _LOGGER.warning(
                "DROP: %s sent %d unreadable or excess Bluetooth adverts", self.name, dropped
            )
        return accepted, dropped


def _parse_advert(item: Any):
    """One advert from the tablet's wire shape, or None if it is unreadable.

    Wire shape (contract with HaBleStream.kt): {"a": "AA:BB:..", "r": -60, "n": name|null,
    "u": [uuid], "sd": {uuid: hex}, "md": {"76": hex}, "tx": int|null, "age": ms}
    """
    try:
        address = str(item["a"]).upper()
        if len(address) != 17:
            return None
        rssi = int(item["r"])
        name = item.get("n") or None
        uuids = [str(u).lower() for u in item.get("u") or []]
        service_data = {str(k).lower(): bytes.fromhex(v) for k, v in (item.get("sd") or {}).items()}
        mfr_data = {int(k): bytes.fromhex(v) for k, v in (item.get("md") or {}).items()}
        tx = item.get("tx")
        tx_power = int(tx) if tx is not None else None
        age = max(0.0, float(item.get("age") or 0) / 1000)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return address, rssi, name, uuids, service_data, mfr_data, tx_power, age


@callback
def async_start_scanner(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device_id: str,
    ha_device_id: str | None,
    channel: Any = None,
) -> tuple[DashieRemoteScanner, CALLBACK_TYPE]:
    """Register the tablet's scanner with HA. Returns it and its unregister callback.

    With a ``channel`` (the tablet has "Let Home Assistant connect" on), the scanner is
    CONNECTABLE: HA may route a connection through the tablet (``ble_connect.py``).
    Without one it only listens, so HA never tries to connect through a tablet that can't.
    """
    source = scanner_source(device_id)
    if channel is not None:
        from .ble_connect import connector  # needs bleak 1.0+; callers check CONNECT_SUPPORTED

        scanner = DashieRemoteScanner(source, entry.title, connector(source, channel), True)
    else:
        scanner = DashieRemoteScanner(source, entry.title, None, False)
    kwargs: dict[str, Any] = {}
    if _REGISTER_TAKES_SOURCE:
        kwargs = {
            "source_domain": entry.domain,
            "source_model": "Dashie",
            "source_config_entry_id": entry.entry_id,
            "source_device_id": ha_device_id,
        }
    unloads = [async_register_scanner(hass, scanner, **kwargs), scanner.async_setup()]
    _LOGGER.info(
        "SMARTHOME_BLE_SCANNER registered %s as %s connectable=%s", entry.title, source, scanner.connectable
    )

    @callback
    def _unload() -> None:
        for unload in unloads:
            unload()
        _LOGGER.info("SMARTHOME_BLE_SCANNER unregistered %s", entry.title)

    return scanner, _unload
