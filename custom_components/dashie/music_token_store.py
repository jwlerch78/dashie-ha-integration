"""Central Music Assistant token store for Dashie.

Stores the MA JWT token centrally in HA so that multiple tablets
can share it without each needing to go through the MA login flow.

Storage: homeassistant.helpers.storage.Store -> .storage/dashie.music_token
HTTP endpoints:
  GET  /api/dashie/music/token  — retrieve stored token + MA URL
  POST /api/dashie/music/token  — save token + MA URL (from first device to login)
"""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "dashie.music_token"
STORAGE_VERSION = 1


class MusicTokenStore:
    """Stores the MA JWT token centrally."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        self._data = data or {}

    def get_token(self) -> dict:
        """Return {token, ma_url} or empty dict."""
        token = self._data.get("token", "")
        ma_url = self._data.get("ma_url", "")
        if token and ma_url:
            return {"token": token, "ma_url": ma_url}
        return {}

    async def async_save_token(self, token: str, ma_url: str) -> None:
        """Store the MA JWT token and URL."""
        self._data = {"token": token, "ma_url": ma_url}
        await self._store.async_save(self._data)
        _LOGGER.info("Saved MA token centrally (url=%s)", ma_url)


# ── HTTP Views ───────────────────────────────────────────────────


class DashieMusicTokenView(HomeAssistantView):
    """Get or save the central MA token."""

    url = "/api/dashie/music/token"
    name = "api:dashie:music:token"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: MusicTokenStore | None = hass.data.get("dashie", {}).get("music_token_store")
        if store is None:
            return web.json_response({})
        return web.json_response(store.get_token())

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: MusicTokenStore | None = hass.data.get("dashie", {}).get("music_token_store")
        if store is None:
            return web.json_response({"error": "Store not initialized"}, status=500)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        token = body.get("token", "")
        ma_url = body.get("ma_url", "")
        if not token or not ma_url:
            return web.json_response({"error": "token and ma_url required"}, status=400)

        await store.async_save_token(token, ma_url)
        return web.json_response({"saved": True})


def register_music_token_views(hass: HomeAssistant) -> None:
    """Register music token HTTP views."""
    hass.http.register_view(DashieMusicTokenView())
    _LOGGER.info("Registered Dashie music token views")
