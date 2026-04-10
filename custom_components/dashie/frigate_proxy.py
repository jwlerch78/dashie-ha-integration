"""Frigate API proxy for Dashie.

Proxies Frigate recording/event API calls through the HA integration so
tablets don't need direct network access to the Frigate container. Handles
auto-detection of the Frigate URL within the Docker network.

Endpoints:
  GET /api/dashie/frigate/cameras
  GET /api/dashie/frigate/events
  GET /api/dashie/frigate/recordings/{camera}/summary
  GET /api/dashie/frigate/{camera}/{start}/{end}/clip.mp4
  GET /api/dashie/frigate/event/{event_id}/clip.mp4
  GET /api/dashie/frigate/event/{event_id}/thumbnail.jpg

Auth: HA Bearer token (requires_auth = True)
"""
from __future__ import annotations

import logging

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Frigate API candidates (Docker hostnames)
_FRIGATE_CANDIDATES = [
    "http://ccab4aaf-frigate-fa:5000",
    "http://ccab4aaf-frigate:5000",
    "http://frigate:5000",
    "http://localhost:5000",
]

# Shared session + detected URL (module-level, set on first successful probe)
_frigate_url: str | None = None
_session: aiohttp.ClientSession | None = None
_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)
_STREAM_TIMEOUT = aiohttp.ClientTimeout(total=300, connect=5)  # Clips can be long


async def _get_session() -> aiohttp.ClientSession:
    """Get or create a reusable HTTP session."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=_TIMEOUT)
    return _session


async def _detect_frigate() -> str | None:
    """Probe for Frigate API and cache the URL."""
    global _frigate_url
    if _frigate_url:
        return _frigate_url

    session = await _get_session()
    for url in _FRIGATE_CANDIDATES:
        try:
            async with session.get(f"{url}/api/version", timeout=_TIMEOUT) as resp:
                if resp.status == 200:
                    version = await resp.text()
                    _LOGGER.info("Found Frigate %s at %s", version.strip(), url)
                    _frigate_url = url
                    return url
        except Exception:
            continue

    _LOGGER.warning("Frigate not found at any candidate URL")
    return None


async def _proxy_json(request: web.Request, path: str, params: dict | None = None) -> web.Response:
    """Proxy a JSON GET request to Frigate."""
    base = await _detect_frigate()
    if not base:
        return web.json_response({"error": "Frigate not available"}, status=502)

    session = await _get_session()
    try:
        url = f"{base}{path}"
        async with session.get(url, params=params, timeout=_TIMEOUT) as resp:
            data = await resp.json()
            return web.json_response(data, status=resp.status)
    except Exception as err:
        _LOGGER.error("Frigate proxy error (%s): %s", path, err)
        return web.json_response({"error": str(err)}, status=502)


async def _proxy_stream(request: web.Request, path: str) -> web.StreamResponse:
    """Proxy a binary stream (clip/thumbnail) from Frigate."""
    base = await _detect_frigate()
    if not base:
        return web.json_response({"error": "Frigate not available"}, status=502)

    session = await _get_session()
    try:
        url = f"{base}{path}"
        async with session.get(url, timeout=_STREAM_TIMEOUT) as resp:
            if resp.status != 200:
                return web.Response(status=resp.status, body=await resp.read())

            response = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": resp.headers.get("Content-Type", "video/mp4"),
                },
            )
            if "Content-Length" in resp.headers:
                response.headers["Content-Length"] = resp.headers["Content-Length"]

            await response.prepare(request)
            async for chunk in resp.content.iter_chunked(65536):
                await response.write(chunk)
            await response.write_eof()
            return response
    except Exception as err:
        _LOGGER.error("Frigate stream proxy error (%s): %s", path, err)
        return web.json_response({"error": str(err)}, status=502)


# ── Views ──────────────────────────────────────────────────────────


class FrigateCamerasView(HomeAssistantView):
    """GET /api/dashie/frigate/cameras — list Frigate cameras."""

    url = "/api/dashie/frigate/cameras"
    name = "api:dashie:frigate:cameras"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        base = await _detect_frigate()
        if not base:
            return web.json_response({"error": "Frigate not available"}, status=502)

        session = await _get_session()
        try:
            async with session.get(f"{base}/api/config", timeout=_TIMEOUT) as resp:
                config = await resp.json()
                cameras = list(config.get("cameras", {}).keys())
                return web.json_response({"cameras": cameras})
        except Exception as err:
            _LOGGER.error("Frigate cameras error: %s", err)
            return web.json_response({"error": str(err)}, status=502)


class FrigateEventsView(HomeAssistantView):
    """GET /api/dashie/frigate/events?camera=X&after=ts&before=ts&limit=50."""

    url = "/api/dashie/frigate/events"
    name = "api:dashie:frigate:events"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        params = {}
        for key in ("camera", "after", "before", "limit", "label", "has_clip"):
            val = request.query.get(key)
            if val:
                params[key] = val
        if "limit" not in params:
            params["limit"] = "50"

        return await _proxy_json(request, "/api/events", params)


class FrigateRecordingSummaryView(HomeAssistantView):
    """GET /api/dashie/frigate/recordings/{camera}/summary."""

    url = "/api/dashie/frigate/recordings/{camera}/summary"
    name = "api:dashie:frigate:recordings:summary"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        camera = request.match_info["camera"]
        params = {}
        for key in ("after", "before"):
            val = request.query.get(key)
            if val:
                params[key] = val

        return await _proxy_json(request, f"/api/{camera}/recordings/summary", params)


class FrigateClipView(HomeAssistantView):
    """GET /api/dashie/frigate/{camera}/{start}/{end}/clip.mp4."""

    url = "/api/dashie/frigate/{camera}/{start}/{end}/clip.mp4"
    name = "api:dashie:frigate:clip"
    requires_auth = True

    async def get(self, request: web.Request) -> web.StreamResponse:
        camera = request.match_info["camera"]
        start = request.match_info["start"]
        end = request.match_info["end"]
        return await _proxy_stream(request, f"/api/{camera}/start/{start}/end/{end}/clip.mp4")


class FrigateEventClipView(HomeAssistantView):
    """GET /api/dashie/frigate/event/{event_id}/clip.mp4."""

    url = "/api/dashie/frigate/event/{event_id}/clip.mp4"
    name = "api:dashie:frigate:event:clip"
    requires_auth = True

    async def get(self, request: web.Request) -> web.StreamResponse:
        event_id = request.match_info["event_id"]
        return await _proxy_stream(request, f"/api/events/{event_id}/clip.mp4")


class FrigateEventThumbnailView(HomeAssistantView):
    """GET /api/dashie/frigate/event/{event_id}/thumbnail.jpg."""

    url = "/api/dashie/frigate/event/{event_id}/thumbnail.jpg"
    name = "api:dashie:frigate:event:thumbnail"
    requires_auth = True

    async def get(self, request: web.Request) -> web.StreamResponse:
        event_id = request.match_info["event_id"]
        return await _proxy_stream(request, f"/api/events/{event_id}/thumbnail.jpg")


# ── Registration ───────────────────────────────────────────────────


def register_frigate_proxy_views(hass: HomeAssistant) -> None:
    """Register Frigate proxy HTTP views."""
    hass.http.register_view(FrigateCamerasView())
    hass.http.register_view(FrigateEventsView())
    hass.http.register_view(FrigateRecordingSummaryView())
    hass.http.register_view(FrigateClipView())
    hass.http.register_view(FrigateEventClipView())
    hass.http.register_view(FrigateEventThumbnailView())
    _LOGGER.info("Registered Frigate proxy views")
