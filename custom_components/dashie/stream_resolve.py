"""RTSP URL resolution endpoint for Dashie.

Resolves a camera entity ID to a credential-free RTSP restream URL
via go2rtc so tablets can connect directly via ExoPlayer without
credential encoding issues.

Falls back to the raw camera RTSP URL if go2rtc is not available.

Endpoint: GET /api/dashie/stream/resolve/{entity_id}
Auth: HA Bearer token (requires_auth = True)
Response: {"rtsp_url": "rtsp://..."} or {"rtsp_url": null}
"""
from __future__ import annotations

import logging

import aiohttp

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .stream_proxy import _get_stream_source, _redact_url

_LOGGER = logging.getLogger(__name__)

# go2rtc RTSP restream port (default)
_GO2RTC_API_PORT = 1984
_GO2RTC_RTSP_PORT = 8554
_go2rtc_available: bool | None = None  # None = not yet checked
_go2rtc_host: str | None = None


async def _detect_go2rtc(hass: HomeAssistant) -> tuple[bool, str | None]:
    """Detect go2rtc and return (available, host).

    Checks localhost (HA add-on / same host) first.
    """
    global _go2rtc_available, _go2rtc_host
    if _go2rtc_available is not None:
        return _go2rtc_available, _go2rtc_host

    for host in ("127.0.0.1", "localhost"):
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"http://{host}:{_GO2RTC_API_PORT}/api/streams") as resp:
                    if resp.status == 200:
                        _go2rtc_available = True
                        _go2rtc_host = host
                        _LOGGER.info("go2rtc detected at %s:%d", host, _GO2RTC_API_PORT)
                        return True, host
        except Exception:
            continue

    _go2rtc_available = False
    _go2rtc_host = None
    _LOGGER.info("go2rtc not detected — will use raw RTSP URLs")
    return False, None


async def _get_go2rtc_stream_name(host: str, entity_id: str) -> str | None:
    """Check if a camera entity has a go2rtc stream compatible with RTSP restreaming.

    Only returns streams whose producers can be served over go2rtc's RTSP port.
    Streams using echo:curl (HA supervisor API) only work via WebRTC/HTTP, not RTSP.
    """
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"http://{host}:{_GO2RTC_API_PORT}/api/streams") as resp:
                if resp.status != 200:
                    return None
                streams = await resp.json()
                # Check for exact entity_id match first, then common variants.
                # HA creates _hd_stream / _sd_stream sub-entities for Tapo cameras,
                # but go2rtc only knows the base _live_view stream name.
                candidates = [entity_id, f"{entity_id}_live_view"]
                # Strip common suffixes to find the base camera name
                for suffix in ("_hd_stream", "_sd_stream", "_live_view"):
                    if entity_id.endswith(suffix):
                        base = entity_id[: -len(suffix)]
                        candidates.extend([base, f"{base}_live_view"])
                        break
                for candidate in candidates:
                    if candidate not in streams:
                        continue
                    # Check if any producer has a direct stream URL (rtsp://, rtmp://)
                    # that go2rtc can restream over its RTSP port. Streams with only
                    # echo:curl or exec: producers don't work via RTSP restream.
                    stream_info = streams[candidate]
                    producers = stream_info.get("producers") or []
                    has_direct = False
                    for prod in producers:
                        url = ""
                        if isinstance(prod, dict):
                            url = prod.get("url", "")
                        elif isinstance(prod, str):
                            url = prod
                        if url.startswith(("rtsp://", "rtmp://", "rtsps://")):
                            has_direct = True
                            break
                        # Active producer with no URL but has a format_name means
                        # the stream is currently connected (e.g. incoming RTSP)
                        if isinstance(prod, dict) and prod.get("format_name") == "rtsp":
                            has_direct = True
                            break
                    if has_direct:
                        return candidate
                    _LOGGER.debug(
                        "go2rtc stream %s exists but has no direct producer "
                        "(only echo/exec) — skipping RTSP restream",
                        candidate,
                    )
                return None
    except Exception:
        return None


class DashieStreamResolveView(HomeAssistantView):
    """Resolve a camera entity to its RTSP stream source URL."""

    url = "/api/dashie/stream/resolve/{entity_id:.*}"
    name = "api:dashie:stream:resolve"
    requires_auth = True

    async def get(self, request: web.Request, entity_id: str) -> web.Response:
        """Resolve entity to RTSP URL.

        Prefers go2rtc restream URL (no credentials, ExoPlayer-friendly).
        Falls back to raw camera RTSP URL if go2rtc unavailable.
        """
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

        # Prefer go2rtc restream (credential-free, ExoPlayer-friendly)
        go2rtc_ok, go2rtc_host = await _detect_go2rtc(hass)
        if go2rtc_ok and go2rtc_host:
            stream_name = await _get_go2rtc_stream_name(go2rtc_host, entity_id)
            if stream_name:
                # Use HA's IP (visible to tablets) not localhost
                ha_ip = request.host.split(":")[0]
                rtsp_url = f"rtsp://{ha_ip}:{_GO2RTC_RTSP_PORT}/{stream_name}"
                _LOGGER.debug("Resolved %s → %s (via go2rtc)", entity_id, rtsp_url)
                return web.json_response({"rtsp_url": rtsp_url})

        # Fallback: raw camera RTSP URL (may contain credentials)
        rtsp_url = await _get_stream_source(hass, entity_id)
        _LOGGER.debug(
            "Resolved %s → %s (raw)",
            entity_id, _redact_url(rtsp_url) if rtsp_url else None,
        )
        return web.json_response({"rtsp_url": rtsp_url})


def register_stream_resolve_views(hass: HomeAssistant) -> None:
    """Register stream resolve HTTP views."""
    hass.http.register_view(DashieStreamResolveView())
    _LOGGER.info("Registered Dashie stream resolve view")
