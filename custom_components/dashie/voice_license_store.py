"""Central voice-license store for Dashie household sharing.

A tablet with a PAID voice license publishes it here so other tablets on this
same HA instance can adopt it: an unlicensed tablet reads the seed license and
asks the Dashie license server to mint its OWN per-device key (capped at 10 per
seed). Mirrors immich_token_store.py — HA-token-gated (requires_auth), so only a
device genuinely on this HA instance can read the seed.

Storage: homeassistant.helpers.storage.Store -> .storage/dashie.voice_license
HTTP endpoints:
  GET    /api/dashie/voice/license  — retrieve the household seed license
  POST   /api/dashie/voice/license  — publish a seed license (from a licensed device)
  DELETE /api/dashie/voice/license  — clear it
"""
from __future__ import annotations

import logging
import time

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "dashie.voice_license"
STORAGE_VERSION = 1


class VoiceLicenseStore:
    """Stores the household's seed voice license centrally."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        self._data = data or {}

    def get_license(self) -> dict:
        """Return {seed_device_id, seed_license_key, household_id, sharing_enabled} or {}."""
        seed_device_id = self._data.get("seed_device_id", "")
        seed_license_key = self._data.get("seed_license_key", "")
        if seed_device_id and seed_license_key:
            return {
                "seed_device_id": seed_device_id,
                "seed_license_key": seed_license_key,
                "household_id": self._data.get("household_id", ""),
                "sharing_enabled": self._data.get("sharing_enabled", True),
                "updated_at": self._data.get("updated_at", 0),
            }
        return {}

    async def async_save_license(
        self, seed_device_id: str, seed_license_key: str, household_id: str
    ) -> None:
        """Publish a seed license for household sharing."""
        self._data = {
            "seed_device_id": seed_device_id,
            "seed_license_key": seed_license_key,
            "household_id": household_id,
            "sharing_enabled": True,
            "updated_at": int(time.time()),
        }
        await self._store.async_save(self._data)
        _LOGGER.info("Saved household voice seed license (device=%s)", seed_device_id)

    async def async_clear(self) -> None:
        """Clear the stored seed license (stop sharing)."""
        self._data = {}
        await self._store.async_save(self._data)
        _LOGGER.info("Cleared voice license store")


def _household_id(hass: HomeAssistant) -> str:
    """Stable household id = this integration's config-entry id."""
    entries = hass.config_entries.async_entries("dashie")
    return entries[0].entry_id if entries else ""


# ── HTTP Views ───────────────────────────────────────────────────


class DashieVoiceLicenseView(HomeAssistantView):
    """Get or publish the household seed voice license."""

    url = "/api/dashie/voice/license"
    name = "api:dashie:voice:license"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: VoiceLicenseStore | None = hass.data.get("dashie", {}).get(
            "voice_license_store"
        )
        if store is None:
            return web.json_response({})
        return web.json_response(store.get_license())

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: VoiceLicenseStore | None = hass.data.get("dashie", {}).get(
            "voice_license_store"
        )
        if store is None:
            return web.json_response({"error": "Store not initialized"}, status=500)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        device_id = body.get("device_id", "")
        license_key = body.get("license_key", "")
        if not device_id or not license_key:
            return web.json_response(
                {"error": "device_id and license_key required"}, status=400
            )

        household_id = _household_id(hass)
        await store.async_save_license(device_id, license_key, household_id)
        return web.json_response({"saved": True, "household_id": household_id})

    async def delete(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: VoiceLicenseStore | None = hass.data.get("dashie", {}).get(
            "voice_license_store"
        )
        if store is None:
            return web.json_response({})
        await store.async_clear()
        return web.json_response({"cleared": True})


def register_voice_license_views(hass: HomeAssistant) -> None:
    """Register voice-license HTTP views."""
    hass.http.register_view(DashieVoiceLicenseView())
    _LOGGER.info("Registered Dashie voice license views")
