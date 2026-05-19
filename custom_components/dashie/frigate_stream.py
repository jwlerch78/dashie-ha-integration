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
    response: web.StreamResponse,
    camera_name: str,
    fps: int,
    width: int | None,
) -> bool:
    """Proxy Frigate's native MJPEG endpoint to ``response``.

    Frigate's MJPEG is a much cheaper path than running ffmpeg over the
    HA camera entity — Frigate is already decoding the camera and can
    encode JPEGs directly.

    Returns:
        True  — Frigate served at least the headers successfully (any
                subsequent disconnect is treated as a normal client exit).
        False — Frigate is not detected, or it returned a non-200 before
                we wrote any bytes. Caller may attempt the legacy ffmpeg
                path. **Do not call this twice for one response** — once
                ``response.prepare()`` has run downstream we can't fall
                back.

    The caller owns the ``StreamResponse`` headers and ``prepare()``; we
    only write body chunks here.

    ``width`` is passed to Frigate as ``h`` (height). Frigate accepts
    only a height parameter and preserves aspect ratio, so the existing
    tablet-side ``width`` value is a close enough hint — the cards
    auto-scale on render anyway.
    """
    base = await _detect_frigate()
    if not base:
        return False

    params: dict[str, str] = {"fps": str(fps)}
    if width:
        params["h"] = str(width)

    url = f"{base}/api/{camera_name}"
    session = await _get_session()
    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                _LOGGER.warning(
                    "Frigate MJPEG %s returned HTTP %d — falling back",
                    camera_name, resp.status,
                )
                return False

            async for chunk in resp.content.iter_any():
                if not chunk:
                    continue
                try:
                    await response.write(chunk)
                except (ConnectionResetError, asyncio.CancelledError):
                    # Tablet closed the card — normal exit.
                    return True
            return True
    except asyncio.CancelledError:
        # Stream cancelled by caller — propagate, not a Frigate failure.
        raise
    except Exception as err:
        _LOGGER.info(
            "Frigate MJPEG stream %s ended: %s: %s",
            camera_name, err.__class__.__name__, err,
        )
        # We may have written body bytes already; returning True signals
        # the caller "do not retry on the legacy path".
        return True
