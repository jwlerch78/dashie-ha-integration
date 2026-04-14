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

import asyncio
import logging
import os
from urllib.parse import urlparse

import aiohttp

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .stream_proxy import _get_stream_source, _redact_url
from .go2rtc_manager import Go2RtcManager

_LOGGER = logging.getLogger(__name__)

# go2rtc detection (legacy, used by _detect_go2rtc/_get_go2rtc_stream_name)
_GO2RTC_API_PORT = 1984
_GO2RTC_RTSP_PORT = 8554
_go2rtc_available: bool | None = None
_go2rtc_host: str | None = None

# Shared go2rtc manager — initialized in __init__.py
_manager: Go2RtcManager | None = None


def set_go2rtc_manager(manager: Go2RtcManager) -> None:
    """Set the shared go2rtc manager (called from __init__.py)."""
    global _manager
    _manager = manager


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


async def _is_rtsp_reachable(rtsp_url: str, timeout: float = 2.0) -> bool:
    """Quick TCP connect check to verify the RTSP source host:port is reachable."""
    try:
        parsed = urlparse(rtsp_url)
        host = parsed.hostname
        port = parsed.port or 554
        if not host:
            return False
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


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
        content = config_path.read_text()
        config = yaml.safe_load(content) or {}
        streams = config.get("streams") or {}

        if stream_name in streams:
            _LOGGER.debug("go2rtc stream %s already in config", stream_name)
            return True

        # Append to YAML file directly to preserve existing formatting.
        # If streams: is empty ({}) or missing, replace the line.
        if not streams:
            # Replace "streams: {}" or add "streams:" section
            if "streams:" in content:
                content = content.replace("streams: {}", "streams:")
                content = content.replace("streams:{}", "streams:")
            else:
                content = content.rstrip() + "\nstreams:\n"

        # Append the new stream entry
        content = content.rstrip() + f"\n  {stream_name}:\n  - '{rtsp_url}'\n"
        config_path.write_text(content)
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
                    token = os.environ.get("SUPERVISOR_TOKEN", "")
                    headers = {"Authorization": f"Bearer {token}"}
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        # Find go2rtc addon slug dynamically
                        slug = None
                        async with session.get(
                            "http://supervisor/addons",
                            headers=headers,
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                addons = data.get("data", {}).get("addons", [])
                                for addon in addons:
                                    if "go2rtc" in addon.get("name", "").lower() or \
                                       "go2rtc" in addon.get("slug", "").lower():
                                        slug = addon["slug"]
                                        break
                        if not slug:
                            _LOGGER.warning("Could not find go2rtc addon to restart")
                            return

                        async with session.post(
                            f"http://supervisor/addons/{slug}/restart",
                            headers=headers,
                        ) as resp:
                            if resp.status == 200:
                                _LOGGER.info(
                                    "Restarted go2rtc addon (%s) to load new streams",
                                    slug,
                                )
                            else:
                                body = await resp.text()
                                _LOGGER.warning(
                                    "Failed to restart go2rtc addon %s: HTTP %d: %s",
                                    slug, resp.status, body,
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

        Query params:
          ?check_only=1  — return availability + existing go2rtc URL only,
                           skip auto-registration (used by strip for offline detection)
        """
        hass: HomeAssistant = request.app["hass"]
        check_only = request.query.get("check_only") == "1"

        if not entity_id.startswith("camera."):
            return web.json_response(
                {"error": f"Not a camera entity: '{entity_id}'"}, status=400
            )

        state = hass.states.get(entity_id)
        if not state:
            return web.json_response(
                {"error": f"Entity '{entity_id}' not found"}, status=404
            )

        # Entity availability — "idle" is normal (camera on, not actively viewed).
        # Only "unavailable" (offline/disconnected) and "off" (explicitly disabled)
        # mean the camera can't stream.
        available = state.state not in ("unavailable", "off")

        # Get the raw RTSP source URL from HA
        raw_rtsp = await _get_stream_source(hass, entity_id)
        has_credentials = bool(
            raw_rtsp
            and "@" in raw_rtsp.split("//", 1)[-1].split("/", 1)[0]
        )

        # All RTSP streams go through go2rtc — it handles auth for credentialed
        # streams and fixes malformed Content-Base headers from some RTSP servers
        # (e.g. pedroSG94 returns Content-Base: rtsp://:0/ which breaks ExoPlayer).
        if raw_rtsp:
            reachable = await _is_rtsp_reachable(raw_rtsp)
            if not reachable:
                _LOGGER.info(
                    "Credentialed RTSP unreachable for %s: %s",
                    entity_id, _redact_url(raw_rtsp),
                )
                return web.json_response({
                    "rtsp_url": None,
                    "available": False,
                })

            if check_only:
                # Just confirm availability — don't register yet
                return web.json_response({
                    "rtsp_url": None,
                    "available": available,
                })

            # Prefer sub-stream for tablet playback
            reg_url = raw_rtsp
            if "/stream1" in reg_url:
                reg_url = reg_url.replace("/stream1", "/stream2")
                _LOGGER.debug(
                    "Substituted /stream1 → /stream2 for tablet-friendly resolution"
                )

            # Credential-free sources: connect ExoPlayer directly (lower latency,
            # avoids go2rtc TCP relay buffer that adds burstiness).
            # Credentialed sources: must go through go2rtc to strip credentials.
            if not has_credentials:
                _LOGGER.info(
                    "Resolved %s → %s (direct, no credentials)",
                    entity_id, _redact_url(reg_url),
                )
                return web.json_response({
                    "rtsp_url": reg_url,
                    "available": available,
                })

            # Register in go2rtc (existing or managed subprocess)
            if _manager and await _manager.ensure():
                rtsp_url_template = await _manager.register_stream(entity_id, reg_url)
                if rtsp_url_template:
                    ha_ip = request.host.split(":")[0]
                    rtsp_url = rtsp_url_template.replace("{ha_ip}", ha_ip)
                    _LOGGER.info(
                        "Resolved %s → %s (via go2rtc)",
                        entity_id, rtsp_url,
                    )
                    return web.json_response({
                        "rtsp_url": rtsp_url,
                        "available": available,
                    })

        # No RTSP available — tablet will use MJPEG fallback
        rtsp_url = None
        return web.json_response({"rtsp_url": rtsp_url, "available": available})


def register_stream_resolve_views(hass: HomeAssistant) -> None:
    """Register stream resolve HTTP views."""
    hass.http.register_view(DashieStreamResolveView())
    _LOGGER.info("Registered Dashie stream resolve view")
