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
    """Probe for Frigate API and cache the URL.

    If a cached URL exists but no longer responds, callers should set
    `_frigate_url = None` to force a re-probe (see feed_registry._get_frigate_camera_names
    for the self-heal path).
    """
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

    _LOGGER.warning("Frigate not found at any candidate URL: %s", _FRIGATE_CANDIDATES)
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
