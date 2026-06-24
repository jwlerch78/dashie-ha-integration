"""Frigate-routed live stream helpers for Dashie video feeds.

When a feed is matched to a Frigate camera, prefer routing the LIVE stream
through Frigate's restream (embedded go2rtc) and MJPEG endpoints instead
of resolving via the underlying HA camera entity. Frigate connects to the
cameras directly over RTSP and is independent of fragile upstream HA
integrations (e.g. tapo_control), so a Frigate-routed feed stays up even
when the camera's HA entity goes ``unavailable``.

Public API:
    build_frigate_rtsp_url(ha_host, camera_name) -> str
    is_frigate_rtsp_reachable(rtsp_url) -> bool
    stream_frigate_mjpeg(response, camera_name, fps, width) -> bool

URL conventions assumed here:
    * Frigate's go2rtc RTSP restream is reachable at ``rtsp://<ha_host>:8554/<camera>``.
      This is the default when Frigate runs as an HA add-on; users with
      non-default port mappings will fail the probe and fall back to the
      legacy entity path transparently.
    * Frigate's MJPEG endpoint is ``GET <frigate_url>/api/<camera>?fps=&h=``.
      We discover ``<frigate_url>`` via the existing ``_detect_frigate()``
      in :mod:`.frigate_proxy` (Docker-internal hostname, reachable from HA
      but not from the tablet — so the integration proxies the stream).
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from aiohttp import web

from .frigate_proxy import _detect_frigate, _get_session

_LOGGER = logging.getLogger(__name__)

# Frigate's embedded go2rtc RTSP port. The Frigate HA add-on exposes this
# on the HA host by default; if the user re-mapped it the probe in
# ``is_frigate_rtsp_reachable`` will fail and the caller falls back to
# the legacy entity-resolution path.
_FRIGATE_RTSP_PORT = 8554

# Short TCP-connect timeout for the reachability probe. The HA-side
# resolve handler is in the latency hot path for opening the strip, so
# we keep this aggressive — a slow Frigate is treated as unreachable
# and the feed falls back to the entity path.
_FRIGATE_PROBE_TIMEOUT = 2.0

# How long to wait for Frigate's MJPEG endpoint to emit its first frame
# before giving up and falling back to the legacy entity+ffmpeg path.
# Frigate can return 200 yet never send a JPEG when the camera is
# unavailable or detect-only, which would otherwise spin the card forever.
_FRIGATE_FIRST_FRAME_TIMEOUT = 5.0


def build_frigate_rtsp_url(ha_host: str, camera_name: str) -> str:
    """Build the Frigate restream RTSP URL the tablet will connect to.

    Frigate ships go2rtc embedded; its RTSP port is exposed on the HA
    host. ExoPlayer connects directly to ``rtsp://<ha_host>:8554/<camera>``
    — no credentials, clean Content-Base header, low latency.
    """
    return f"rtsp://{ha_host}:{_FRIGATE_RTSP_PORT}/{camera_name}"


async def is_frigate_rtsp_reachable(rtsp_url: str) -> bool:
    """Quick TCP-connect probe to verify Frigate's restream port is up.

    Returns False on any error (DNS, refused, timeout) so callers can
    transparently fall back to the legacy entity path.
    """
    try:
        parsed = urlparse(rtsp_url)
        host = parsed.hostname
        port = parsed.port or _FRIGATE_RTSP_PORT
        if not host:
            return False
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=_FRIGATE_PROBE_TIMEOUT,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def stream_frigate_mjpeg(
    request: web.Request,
    camera_name: str,
    fps: int,
    width: int | None,
) -> web.StreamResponse | None:
    """Proxy Frigate's native MJPEG endpoint for ``camera_name``.

    Frigate's MJPEG is a much cheaper path than running ffmpeg over the
    HA camera entity — Frigate is already decoding the camera and can
    encode JPEGs directly.

    Crucially, this confirms Frigate is actually serving frames for this
    camera *before* committing our own response. We open the Frigate
    stream, require a 200, and peek the first non-empty chunk (bounded by
    ``_FRIGATE_FIRST_FRAME_TIMEOUT``). Only then do we ``prepare()`` the
    multipart response and stream. This is what lets a Frigate failure
    fall back cleanly to the legacy entity+ffmpeg path.

    The previous version prepared the response first and only then hit
    Frigate, so a non-200 *or* a 200-with-no-frames (camera offline /
    detect-only) left the tablet committed to an empty 200 multipart body
    — the card spun forever with no fallback. (Regression from Phase A
    Frigate auto-routing.)

    Returns:
        web.StreamResponse — Frigate served a real frame; the response is
                prepared and fully streamed. The caller returns it as-is.
        None — Frigate is not detected, returned non-200, or produced no
                frame in time. The request is still uncommitted, so the
                caller may fall back to the legacy entity+ffmpeg path.

    ``width`` is passed to Frigate as ``h`` (height). Frigate accepts
    only a height parameter and preserves aspect ratio, so the existing
    tablet-side ``width`` value is a close enough hint — the cards
    auto-scale on render anyway.
    """
    base = await _detect_frigate()
    if not base:
        return None

    params: dict[str, str] = {"fps": str(fps)}
    if width:
        params["h"] = str(width)

    url = f"{base}/api/{camera_name}"
    session = await _get_session()

    try:
        resp = await session.get(url, params=params)
    except asyncio.CancelledError:
        raise
    except Exception as err:
        _LOGGER.info(
            "Frigate MJPEG connect for %s failed: %s: %s",
            camera_name, err.__class__.__name__, err,
        )
        return None

    # Once we have ``resp`` we own it — release on every exit path.
    out: web.StreamResponse | None = None
    try:
        if resp.status != 200:
            _LOGGER.warning(
                "Frigate MJPEG %s returned HTTP %d — falling back",
                camera_name, resp.status,
            )
            return None

        # Peek the first real frame. Frigate sometimes returns 200 but
        # never emits a JPEG (camera unavailable / detect-only); treat
        # that as a failure so the caller falls back instead of spinning.
        first_chunk: bytes | None = None
        try:
            async with asyncio.timeout(_FRIGATE_FIRST_FRAME_TIMEOUT):
                async for chunk in resp.content.iter_any():
                    if chunk:
                        first_chunk = chunk
                        break
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Frigate MJPEG %s produced no frame in %.0fs — falling back",
                camera_name, _FRIGATE_FIRST_FRAME_TIMEOUT,
            )
            return None

        if not first_chunk:
            _LOGGER.warning(
                "Frigate MJPEG %s closed before any frame — falling back",
                camera_name,
            )
            return None

        # Frigate is serving — now (and only now) commit our response.
        out = web.StreamResponse(
            headers={
                "Content-Type": "multipart/x-mixed-replace; boundary=frame",
                "Cache-Control": "no-cache, no-store",
                "Connection": "keep-alive",
            }
        )
        await out.prepare(request)

        try:
            await out.write(first_chunk)
            async for chunk in resp.content.iter_any():
                if not chunk:
                    continue
                await out.write(chunk)
        except ConnectionResetError:
            # Tablet closed the card — normal exit.
            pass
        return out
    except asyncio.CancelledError:
        # Stream cancelled by caller — propagate, not a Frigate failure.
        raise
    except Exception as err:
        _LOGGER.info(
            "Frigate MJPEG stream %s ended: %s: %s",
            camera_name, err.__class__.__name__, err,
        )
        # If ``out`` is prepared we've committed — return it (no fallback).
        # Otherwise the request is uncommitted; None lets the caller retry.
        return out
    finally:
        resp.release()
