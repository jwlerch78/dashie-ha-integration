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
import os

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


_GO2RTC_CONFIG_PATH = "/config/go2rtc.yaml"
_go2rtc_restart_pending = False


async def _register_go2rtc_stream(
    hass: HomeAssistant, stream_name: str, rtsp_url: str
) -> bool:
    """Register a stream in go2rtc by adding it to go2rtc.yaml and restarting.

    API-registered streams (POST /api/streams) are ephemeral and don't serve
    on go2rtc's RTSP port. Only YAML-configured streams work for RTSP restreaming.
    """
    import yaml
    from pathlib import Path

    global _go2rtc_restart_pending

    config_path = Path(_GO2RTC_CONFIG_PATH)
    if not config_path.exists():
        _LOGGER.warning("go2rtc config not found at %s", _GO2RTC_CONFIG_PATH)
        return False

    try:
        config = yaml.safe_load(config_path.read_text()) or {}
        streams = config.setdefault("streams", {})

        if stream_name in streams:
            _LOGGER.debug("go2rtc stream %s already in config", stream_name)
            return True

        streams[stream_name] = [rtsp_url]
        config_path.write_text(yaml.dump(config, default_flow_style=False))
        _LOGGER.info(
            "Added go2rtc stream %s → %s to config",
            stream_name,
            _redact_url(rtsp_url),
        )

        # Restart go2rtc addon to pick up new config.
        # Batch restarts — if multiple streams are registered in quick succession,
        # only restart once after a short delay.
        if not _go2rtc_restart_pending:
            _go2rtc_restart_pending = True

            async def _delayed_restart():
                global _go2rtc_restart_pending
                import asyncio
                await asyncio.sleep(3)  # Wait for other streams to register
                _go2rtc_restart_pending = False
                try:
                    timeout = aiohttp.ClientTimeout(total=10)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        # HA Supervisor API to restart go2rtc addon
                        async with session.post(
                            "http://supervisor/addons/d490ac36_go2rtc/restart",
                            headers={
                                "Authorization": f"Bearer {os.environ.get('SUPERVISOR_TOKEN', '')}",
                            },
                        ) as resp:
                            if resp.status == 200:
                                _LOGGER.info("Restarted go2rtc addon to load new streams")
                            else:
                                _LOGGER.warning(
                                    "Failed to restart go2rtc addon: HTTP %d",
                                    resp.status,
                                )
                except Exception as err:
                    _LOGGER.warning("Failed to restart go2rtc addon: %s", err)

            hass.async_create_task(_delayed_restart())

        return True
    except Exception as err:
        _LOGGER.warning("Failed to register go2rtc stream %s: %s", stream_name, err)
        return False


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

        # Entity availability based on HA state (unavailable = camera offline)
        available = state.state != "unavailable"

        # Prefer go2rtc restream (credential-free, ExoPlayer-friendly)
        go2rtc_ok, go2rtc_host = await _detect_go2rtc(hass)
        if go2rtc_ok and go2rtc_host:
            stream_name = await _get_go2rtc_stream_name(go2rtc_host, entity_id)
            if stream_name:
                ha_ip = request.host.split(":")[0]
                rtsp_url = f"rtsp://{ha_ip}:{_GO2RTC_RTSP_PORT}/{stream_name}"
                _LOGGER.debug("Resolved %s → %s (via go2rtc)", entity_id, rtsp_url)
                return web.json_response({
                    "rtsp_url": rtsp_url,
                    "available": available,
                })

            # go2rtc is available but doesn't have this stream — auto-register it.
            # Get the raw RTSP URL from HA and register in go2rtc.
            raw_rtsp = await _get_stream_source(hass, entity_id)
            if raw_rtsp:
                # Use entity_id as the go2rtc stream name for consistency
                registered = await _register_go2rtc_stream(
                    hass, entity_id, raw_rtsp
                )
                if registered:
                    ha_ip = request.host.split(":")[0]
                    rtsp_url = (
                        f"rtsp://{ha_ip}:{_GO2RTC_RTSP_PORT}/{entity_id}"
                    )
                    _LOGGER.info(
                        "Auto-registered and resolved %s → %s", entity_id, rtsp_url
                    )
                    return web.json_response({
                        "rtsp_url": rtsp_url,
                        "available": available,
                    })

        # Fallback: raw camera RTSP URL — but only if credential-free.
        # URLs with userinfo (user:pass@host) break android.net.Uri when
        # the username contains '@' (e.g. email addresses). Return null
        # so the tablet falls back to MJPEG instead of failing repeatedly.
        rtsp_url = await _get_stream_source(hass, entity_id)
        if rtsp_url and "@" in rtsp_url.split("//", 1)[-1].split("/", 1)[0]:
            _LOGGER.debug(
                "Resolved %s → credential URL (suppressed for ExoPlayer safety)",
                entity_id,
            )
            rtsp_url = None
        else:
            _LOGGER.debug(
                "Resolved %s → %s (raw)",
                entity_id, _redact_url(rtsp_url) if rtsp_url else None,
            )
        return web.json_response({"rtsp_url": rtsp_url, "available": available})


def register_stream_resolve_views(hass: HomeAssistant) -> None:
    """Register stream resolve HTTP views."""
    hass.http.register_view(DashieStreamResolveView())
    _LOGGER.info("Registered Dashie stream resolve view")
