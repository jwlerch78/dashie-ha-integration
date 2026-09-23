"""Frigate API proxy for Dashie.

Proxies Frigate recording/event API calls through the HA integration so
tablets don't need direct network access to the Frigate container. Handles
auto-detection of the Frigate URL: the official Frigate integration's configured
URL first, then the known add-on hostnames.

The proxy sends no Frigate credentials, so it needs Frigate's unauthenticated
internal API port (5000). An install reachable only on the authenticated port
(8971) is not supported.

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
from homeassistant.core import HomeAssistant, async_get_hass
from homeassistant.exceptions import HomeAssistantError

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
# D-101: a household that will never run Frigate must not be told so every 30s.
# The miss is worth ONE loud line per process; after that it is debug. Reset on a
# successful detect so a Frigate that appears and later vanishes warns again.
_not_found_warned: bool = False
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
    """Probe for Frigate API and cache the URL.

    If a cached URL exists but no longer responds, callers should set
    `_frigate_url = None` to force a re-probe (see feed_registry._get_frigate_camera_names
    for the self-heal path).
    """
    global _frigate_url, _not_found_warned
    if _frigate_url:
        return _frigate_url

    session = await _get_session()
    entry_urls = _frigate_integration_urls()
    for url in [*entry_urls, *(u for u in _FRIGATE_CANDIDATES if u not in entry_urls)]:
        try:
            async with session.get(f"{url}/api/version", timeout=_TIMEOUT) as resp:
                if resp.status == 200:
                    version = await resp.text()
                    _LOGGER.info("Found Frigate %s at %s", version.strip(), url)
                    _frigate_url = url
                    _not_found_warned = False
                    return url
                reason = f"HTTP {resp.status}"
        except Exception as err:
            reason = type(err).__name__
        if url in entry_urls:
            _LOGGER.warning(
                "DROP: Frigate integration URL %s did not answer /api/version (%s); "
                "the Dashie proxy needs Frigate's unauthenticated port 5000. "
                "Trying the built-in candidates",
                url, reason,
            )

    # 🔴 NOT FINDING FRIGATE IS ONLY A PROBLEM IF THE USER HAS FRIGATE.
    #
    # `entry_urls` is non-empty only when the official Frigate integration is set up. If it
    # is empty, nobody has told us Frigate exists — we probed the built-in add-on hostnames
    # on spec, none answered, and that is the correct and expected outcome on the large
    # majority of installs. Logging it at WARNING made Home Assistant show a household that
    # has never run Frigate a red "This error originated from a custom integration" panel
    # for a non-event (chicknlil, dashie-ha-integration #3: "My dashboard has no Frigate,
    # nor will it").
    #
    # So: absence is DEBUG. A configured Frigate that will not answer is the real fault, and
    # it already warned per-URL above with the reason attached — which is the line worth
    # reading, since it names what went wrong rather than just reporting a miss.
    if entry_urls:
        log = _LOGGER.debug if _not_found_warned else _LOGGER.warning
    else:
        log = _LOGGER.debug
    log("Frigate not found at any candidate URL: %s",
        [*entry_urls, *_FRIGATE_CANDIDATES])
    _not_found_warned = True
    return None


def _frigate_integration_urls() -> list[str]:
    """URLs configured in the official Frigate integration, if it is set up."""
    try:
        hass = async_get_hass()
    except HomeAssistantError:
        _LOGGER.warning("DROP: no hass context; skipping Frigate integration URL lookup")
        return []
    urls = []
    for entry in hass.config_entries.async_entries("frigate"):
        url = str(entry.data.get("url") or "").rstrip("/")
        if url and url not in urls:
            urls.append(url)
    return urls


async def _proxy_json(request: web.Request, path: str, params: dict | None = None) -> web.Response:
    """Proxy a JSON GET request to Frigate."""
    base = await _detect_frigate()
    if not base:
        return web.json_response({"error": "Frigate not available"}, status=502)

    session = await _get_session()
    url = f"{base}{path}"
    try:
        async with session.get(url, params=params, timeout=_TIMEOUT) as resp:
            body_text = await resp.text()
            if resp.status != 200:
                _LOGGER.warning("Frigate returned %s for %s?%s: %s",
                                resp.status, url, params, body_text[:200])
                return web.Response(
                    status=resp.status,
                    body=body_text,
                    content_type=resp.headers.get("Content-Type", "application/json"),
                )
            try:
                data = await resp.json() if body_text and body_text[0] in ("[", "{") else None
            except Exception:
                data = None
            if data is None:
                # Not JSON — return raw text so the caller can see what came back
                return web.Response(
                    status=resp.status,
                    body=body_text,
                    content_type=resp.headers.get("Content-Type", "text/plain"),
                )
            return web.json_response(data, status=resp.status)
    except Exception as err:
        _LOGGER.error("Frigate proxy error (%s %s?%s): %s", type(err).__name__, url, params, err)
        return web.json_response({"error": f"{type(err).__name__}: {err}"}, status=502)


async def _proxy_stream(request: web.Request, path: str) -> web.StreamResponse:
    """Proxy a binary stream (thumbnail) from Frigate — no transcoding."""
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
                    "Content-Type": resp.headers.get("Content-Type", "application/octet-stream"),
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


async def _proxy_clip_transcoded(request: web.Request, path: str) -> web.StreamResponse:
    """Fetch a clip from Frigate and transcode to 720p via FFmpeg for tablet playback.

    Frigate records at the camera's native resolution (e.g., 2560x1440) which may
    exceed tablet hardware decoder capabilities. This pipes the clip through FFmpeg
    to scale to 720p with fast encoding settings.
    """
    import asyncio
    import shutil

    base = await _detect_frigate()
    if not base:
        return web.json_response({"error": "Frigate not available"}, status=502)

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        _LOGGER.warning("FFmpeg not found, falling back to direct proxy")
        return await _proxy_stream(request, path)

    url = f"{base}{path}"
    process: asyncio.subprocess.Process | None = None

    try:
        # Pipe Frigate clip through FFmpeg: scale to 720p, fast encode, stream MP4
        # -movflags frag_keyframe+empty_moov enables streaming (fragmented MP4)
        # stderr=DEVNULL so a full stderr pipe never blocks ffmpeg
        process = await asyncio.create_subprocess_exec(
            ffmpeg_bin,
            "-i", url,
            "-vf", "scale=-2:720",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "fastdecode",
            "-crf", "26",
            "-g", "30",                              # Keyframe every 30 frames (~1s)
            "-c:a", "aac",
            "-b:a", "96k",
            "-movflags", "frag_keyframe+empty_moov",
            "-frag_duration", "1000000",             # 1-second fragments for faster start
            "-f", "mp4",
            "-loglevel", "error",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "video/mp4",
            },
        )
        await response.prepare(request)

        # Stream FFmpeg output to the client. Any exit path (client disconnect,
        # write error, cancellation, normal EOF) falls through to the finally
        # block which guarantees the subprocess is reaped.
        while True:
            chunk = await process.stdout.read(65536)
            if not chunk:
                break
            await response.write(chunk)

        await response.write_eof()
        return response

    except asyncio.CancelledError:
        # Client disconnected — let the finally block clean up, then re-raise.
        raise
    except (ConnectionResetError, aiohttp.ClientConnectionError):
        # Client dropped mid-stream. Subprocess gets killed in finally.
        return web.Response(status=499)
    except Exception as err:
        _LOGGER.error("Frigate clip transcode error (%s): %s", path, err)
        return web.json_response({"error": str(err)}, status=502)
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                _LOGGER.warning("FFmpeg did not exit after kill (%s)", path)


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

    async def get(self, request: web.Request, camera: str) -> web.Response:
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

    async def get(self, request: web.Request, camera: str, start: str, end: str) -> web.StreamResponse:
        return await _proxy_clip_transcoded(request, f"/api/{camera}/start/{start}/end/{end}/clip.mp4")


class FrigateEventClipView(HomeAssistantView):
    """GET /api/dashie/frigate/event/{event_id}/clip.mp4."""

    url = "/api/dashie/frigate/event/{event_id}/clip.mp4"
    name = "api:dashie:frigate:event:clip"
    requires_auth = True

    async def get(self, request: web.Request, event_id: str) -> web.StreamResponse:
        return await _proxy_clip_transcoded(request, f"/api/events/{event_id}/clip.mp4")


class FrigateEventThumbnailView(HomeAssistantView):
    """GET /api/dashie/frigate/event/{event_id}/thumbnail.jpg."""

    url = "/api/dashie/frigate/event/{event_id}/thumbnail.jpg"
    name = "api:dashie:frigate:event:thumbnail"
    requires_auth = True

    async def get(self, request: web.Request, event_id: str) -> web.StreamResponse:
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
