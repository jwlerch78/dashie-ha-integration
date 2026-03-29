"""Central hidden speakers store for Dashie.

Stores the list of speaker IDs hidden across all Dashie tablets.
Each tablet can also have local-only hides (in SharedPreferences).

Storage: homeassistant.helpers.storage.Store -> .storage/dashie.hidden_speakers
HTTP endpoints:
  GET  /api/dashie/music/hidden_speakers  — retrieve hidden speaker IDs
  POST /api/dashie/music/hidden_speakers  — save hidden speaker IDs
"""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "dashie.hidden_speakers"
STORAGE_VERSION = 1


class HiddenSpeakersStore:
    """Stores globally hidden speaker IDs."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        self._data = data or {}

    def get_hidden(self) -> list[str]:
        """Return list of hidden speaker IDs."""
        return self._data.get("hidden", [])

    async def async_save_hidden(self, hidden: list[str]) -> None:
        """Store the hidden speaker IDs."""
        self._data = {"hidden": hidden}
        await self._store.async_save(self._data)
        _LOGGER.info("Saved %d hidden speakers centrally", len(hidden))


# ── HTTP Views ───────────────────────────────────────────────────


class DashieHiddenSpeakersView(HomeAssistantView):
    """Get or save the globally hidden speaker list."""

    url = "/api/dashie/music/hidden_speakers"
    name = "api:dashie:music:hidden_speakers"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: HiddenSpeakersStore | None = hass.data.get("dashie", {}).get(
            "hidden_speakers_store"
        )
        if store is None:
            return web.json_response({"hidden": []})
        return web.json_response({"hidden": store.get_hidden()})

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: HiddenSpeakersStore | None = hass.data.get("dashie", {}).get(
            "hidden_speakers_store"
        )
        if store is None:
            return web.json_response({"error": "Store not initialized"}, status=500)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        hidden = body.get("hidden", [])
        if not isinstance(hidden, list):
            return web.json_response({"error": "hidden must be a list"}, status=400)

        await store.async_save_hidden(hidden)
        return web.json_response({"saved": True, "count": len(hidden)})


def register_hidden_speakers_views(hass: HomeAssistant) -> None:
    """Register hidden speakers HTTP views."""
    hass.http.register_view(DashieHiddenSpeakersView())
    _LOGGER.info("Registered Dashie hidden speakers views")
