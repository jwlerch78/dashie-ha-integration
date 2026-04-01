"""Central Immich credential store for Dashie.

Stores the Immich access token and server URL centrally in HA so that
multiple tablets (including kiosk-mode devices) can share credentials
without each needing to go through the Immich login flow.

Storage: homeassistant.helpers.storage.Store -> .storage/dashie.immich_token
HTTP endpoints:
  GET  /api/dashie/immich/token  — retrieve stored token + server URL + albums
  POST /api/dashie/immich/token  — save token + server URL + albums (from first device to login)
  DELETE /api/dashie/immich/token — clear stored credentials (sign out)
"""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "dashie.immich_token"
STORAGE_VERSION = 1


class ImmichTokenStore:
    """Stores the Immich access token centrally."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        self._data = data or {}

    def get_token(self) -> dict:
        """Return {token, server_url, selected_albums} or empty dict."""
        token = self._data.get("token", "")
        server_url = self._data.get("server_url", "")
        if token and server_url:
            return {
                "token": token,
                "server_url": server_url,
                "selected_albums": self._data.get("selected_albums", ""),
            }
        return {}

    async def async_save_token(
        self, token: str, server_url: str, selected_albums: str = ""
    ) -> None:
        """Store the Immich access token, server URL, and selected albums."""
        self._data = {
            "token": token,
            "server_url": server_url,
            "selected_albums": selected_albums,
        }
        await self._store.async_save(self._data)
        _LOGGER.info("Saved Immich token centrally (url=%s)", server_url)

    async def async_clear(self) -> None:
        """Clear stored credentials (sign out)."""
        self._data = {}
        await self._store.async_save(self._data)
        _LOGGER.info("Cleared Immich token store")


# ── HTTP Views ───────────────────────────────────────────────────


class DashieImmichTokenView(HomeAssistantView):
    """Get or save the central Immich token."""

    url = "/api/dashie/immich/token"
    name = "api:dashie:immich:token"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: ImmichTokenStore | None = hass.data.get("dashie", {}).get(
            "immich_token_store"
        )
        if store is None:
            return web.json_response({})
        return web.json_response(store.get_token())

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: ImmichTokenStore | None = hass.data.get("dashie", {}).get(
            "immich_token_store"
        )
        if store is None:
            return web.json_response({"error": "Store not initialized"}, status=500)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        token = body.get("token", "")
        server_url = body.get("server_url", "")
        if not token or not server_url:
            return web.json_response(
                {"error": "token and server_url required"}, status=400
            )

        selected_albums = body.get("selected_albums", "")
        await store.async_save_token(token, server_url, selected_albums)
        return web.json_response({"saved": True})

    async def delete(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: ImmichTokenStore | None = hass.data.get("dashie", {}).get(
            "immich_token_store"
        )
        if store is None:
            return web.json_response({})
        await store.async_clear()
        return web.json_response({"cleared": True})


def register_immich_token_views(hass: HomeAssistant) -> None:
    """Register Immich token HTTP views."""
    hass.http.register_view(DashieImmichTokenView())
    _LOGGER.info("Registered Dashie Immich token views")
