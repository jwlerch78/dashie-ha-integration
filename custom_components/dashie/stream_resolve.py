"""RTSP URL resolution endpoint for Dashie.

Resolves a camera entity ID to its RTSP stream source URL so tablets
can connect directly via ExoPlayer instead of going through the
MJPEG transcoding proxy.

Endpoint: GET /api/dashie/stream/resolve/{entity_id}
Auth: HA Bearer token (requires_auth = True)
Response: {"rtsp_url": "rtsp://..."} or {"rtsp_url": null}
"""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .stream_proxy import _get_stream_source, _redact_url

_LOGGER = logging.getLogger(__name__)


class DashieStreamResolveView(HomeAssistantView):
    """Resolve a camera entity to its RTSP stream source URL."""

    url = "/api/dashie/stream/resolve/{entity_id:.*}"
    name = "api:dashie:stream:resolve"
    requires_auth = True

    async def get(self, request: web.Request, entity_id: str) -> web.Response:
        """Resolve entity to RTSP URL."""
        hass: HomeAssistant = request.app["hass"]

        if not entity_id.startswith("camera."):
            return web.json_response(
                {"error": f"Not a camera entity: '{entity_id}'"}, status=400
            )

        state = hass.states.get(entity_id)
        if not state:
            return web.json_response(
                {"error": f"Entity '{entity_id}' not found"}, status=404
            )

        rtsp_url = await _get_stream_source(hass, entity_id)
        _LOGGER.debug(
            "Resolved %s → %s",
            entity_id, _redact_url(rtsp_url) if rtsp_url else None,
        )
        return web.json_response({"rtsp_url": rtsp_url})


def register_stream_resolve_views(hass: HomeAssistant) -> None:
    """Register stream resolve HTTP views."""
    hass.http.register_view(DashieStreamResolveView())
    _LOGGER.info("Registered Dashie stream resolve view")
