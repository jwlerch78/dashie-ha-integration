"""Shared MJPEG stream multiplexer for Dashie Video Feeds.

Instead of one FFmpeg process per client, runs one FFmpeg per unique
feed definition and fans out JPEG frames to all connected subscribers.

- First subscriber starts FFmpeg
- Additional subscribers join the existing broadcast
- Last subscriber disconnects -> FFmpeg stops after grace period
- Automatic reconnect on FFmpeg exit (same as stream_proxy.py)

Endpoint: GET /api/dashie/stream/feed/{feed_id}
"""
from __future__ import annotations

import asyncio
import collections
import logging
import time

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .stream_proxy import (
    _build_ffmpeg_cmd,
    _detect_hw_accel,
    _get_stream_source,
    _redact_url,
    DEFAULT_FPS,
    MAX_RECONNECTS,
    READ_CHUNK_SIZE,
    RECONNECT_DELAY,
)

_LOGGER = logging.getLogger(__name__)

# JPEG markers
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"

# Grace period before stopping FFmpeg after last subscriber leaves
GRACE_PERIOD = 5.0
FRAME_BUFFER_SIZE = 3  # Small buffer per subscriber — they share the source


class _SharedStream:
    """A single shared FFmpeg process with fan-out to multiple subscribers."""

    def __init__(self, feed_id: str, hass: HomeAssistant, feed: dict) -> None:
        self.feed_id = feed_id
        self.hass = hass
        self.feed = feed
        self._subscribers: dict[int, asyncio.Queue[bytes | None]] = {}
        self._next_sub_id = 0
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._grace_task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()

    def subscribe(self) -> tuple[int, asyncio.Queue[bytes | None]]:
        """Add a subscriber. Returns (sub_id, frame_queue)."""
        sub_id = self._next_sub_id
        self._next_sub_id += 1
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=FRAME_BUFFER_SIZE)
        self._subscribers[sub_id] = queue
        if self._grace_task and not self._grace_task.done():
            self._grace_task.cancel()
            self._grace_task = None
        _LOGGER.debug(
            "Subscriber %d joined feed %s (total: %d)",
            sub_id, self.feed_id, len(self._subscribers),
        )
        return sub_id, queue

    def unsubscribe(self, sub_id: int) -> int:
        """Remove a subscriber. Returns remaining subscriber count."""
        self._subscribers.pop(sub_id, None)
        remaining = len(self._subscribers)
        _LOGGER.debug(
            "Subscriber %d left feed %s (remaining: %d)",
            sub_id, self.feed_id, remaining,
        )
        return remaining

    async def start(self) -> None:
        """Start the FFmpeg reader loop (with reconnect)."""
        async with self._lock:
            if self._running:
                return
            self._running = True
        self._reader_task = asyncio.ensure_future(self._run_loop())

    async def stop(self) -> None:
        """Stop the FFmpeg process and reader."""
        self._running = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        await self._kill_process()
        # Signal all subscribers to stop
        for queue in self._subscribers.values():
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def schedule_stop(self) -> None:
        """Schedule a graceful stop after GRACE_PERIOD if no subscribers rejoin."""
        async def _delayed_stop():
            await asyncio.sleep(GRACE_PERIOD)
            if not self._subscribers:
                _LOGGER.debug("Grace period expired, stopping feed %s", self.feed_id)
                await self.stop()

        self._grace_task = asyncio.ensure_future(_delayed_stop())

    async def _run_loop(self) -> None:
        """Main loop: start FFmpeg, read frames, broadcast, reconnect on failure."""
        reconnects = 0
        while self._running and reconnects <= MAX_RECONNECTS:
            if reconnects > 0:
                _LOGGER.debug(
                    "Reconnecting feed %s (attempt %d/%d)",
                    self.feed_id, reconnects, MAX_RECONNECTS,
                )
                await asyncio.sleep(RECONNECT_DELAY)

            stream_source = await self._resolve_source()
            if not stream_source:
                _LOGGER.warning("No stream source for feed %s", self.feed_id)
                reconnects += 1
                continue

            hw_accel = await _detect_hw_accel()
            feed = self.feed
            cmd = _build_ffmpeg_cmd(
                stream_source,
                fps=feed.get("fps", DEFAULT_FPS),
                quality=feed.get("quality", 8),
                width=str(feed["resolution"]) if feed.get("resolution") else None,
                hw_accel=hw_accel,
            )
            _LOGGER.debug("Feed %s FFmpeg cmd: %s", self.feed_id, " ".join(_redact_url(c) for c in cmd))

            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                await self._read_and_broadcast()
                # FFmpeg exited
                if not self._running:
                    break
                stderr_data = b""
                try:
                    stderr_data = await asyncio.wait_for(
                        self._process.stderr.read(), timeout=2
                    )
                except asyncio.TimeoutError:
                    pass
                _LOGGER.debug(
                    "FFmpeg exited for feed %s (code=%s): %s",
                    self.feed_id,
                    self._process.returncode,
                    _redact_url(stderr_data.decode(errors="replace")[:300]),
                )
                reconnects += 1
            except asyncio.CancelledError:
                break
            except Exception:
                _LOGGER.exception("Error in feed %s stream loop", self.feed_id)
                break
            finally:
                await self._kill_process()

        self._running = False
        _LOGGER.debug("Feed %s stream ended (reconnects=%d)", self.feed_id, reconnects)

    async def _resolve_source(self) -> str | None:
        """Resolve stream source from feed definition."""
        feed = self.feed
        source_type = feed.get("stream_source_type", "entity")

        if source_type in ("rtsp", "go2rtc") and feed.get("stream_source_url"):
            return feed["stream_source_url"]

        entity_id = feed.get("camera_entity_id")
        if entity_id:
            return await _get_stream_source(self.hass, entity_id)
        return None

    async def _read_and_broadcast(self) -> None:
        """Read JPEG frames from FFmpeg stdout, broadcast to all subscriber queues."""
        reader = self._process.stdout
        buf = b""
        prev_frame = b""
        stats = {"in": 0, "out": 0, "drop": 0}
        log_time = time.monotonic()

        while True:
            chunk = await reader.read(READ_CHUNK_SIZE)
            if not chunk:
                return

            buf += chunk
            while True:
                soi = buf.find(_JPEG_SOI)
                if soi < 0:
                    buf = b""
                    break
                if soi > 0:
                    buf = buf[soi:]
                eoi = buf.find(_JPEG_EOI, 2)
                if eoi < 0:
                    break
                jpeg_data = buf[:eoi + 2]
                buf = buf[eoi + 2:]

                if jpeg_data == prev_frame:
                    continue
                prev_frame = jpeg_data
                stats["in"] += 1

                # Broadcast to all subscribers
                for queue in list(self._subscribers.values()):
                    try:
                        queue.put_nowait(jpeg_data)
                        stats["out"] += 1
                    except asyncio.QueueFull:
                        # Drop oldest, add newest
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        try:
                            queue.put_nowait(jpeg_data)
                        except asyncio.QueueFull:
                            pass
                        stats["drop"] += 1

                now = time.monotonic()
                if now - log_time >= 10.0:
                    elapsed = now - log_time
                    _LOGGER.debug(
                        "Feed %s: in=%.1f fps, out=%.1f fps/sub, subs=%d, drops=%d",
                        self.feed_id,
                        stats["in"] / elapsed,
                        stats["out"] / elapsed / max(len(self._subscribers), 1),
                        len(self._subscribers),
                        stats["drop"],
                    )
                    stats = {"in": 0, "out": 0, "drop": 0}
                    log_time = now

    async def _kill_process(self) -> None:
        """Kill FFmpeg process if running."""
        if self._process:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
            await self._process.wait()
            self._process = None


class StreamMultiplexer:
    """Manages shared streams across all feeds."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._streams: dict[str, _SharedStream] = {}

    async def subscribe(self, feed_id: str, feed: dict) -> tuple[int, asyncio.Queue, _SharedStream]:
        """Subscribe to a feed's shared stream. Starts FFmpeg if needed."""
        if feed_id not in self._streams:
            self._streams[feed_id] = _SharedStream(feed_id, self.hass, feed)

        stream = self._streams[feed_id]
        # Update feed definition in case it changed
        stream.feed = feed
        sub_id, queue = stream.subscribe()

        if not stream._running:
            await stream.start()

        return sub_id, queue, stream

    def unsubscribe(self, feed_id: str, sub_id: int) -> None:
        """Unsubscribe from a feed. Schedules FFmpeg stop if last subscriber."""
        stream = self._streams.get(feed_id)
        if not stream:
            return
        remaining = stream.unsubscribe(sub_id)
        if remaining == 0:
            stream.schedule_stop()

    async def async_shutdown(self) -> None:
        """Stop all streams on HA shutdown."""
        for stream in self._streams.values():
            await stream.stop()
        self._streams.clear()


# ── HTTP View ────────────────────────────────────────────────────


class DashieFeedStreamView(HomeAssistantView):
    """Serve shared MJPEG stream for a feed ID."""

    url = "/api/dashie/stream/feed/{feed_id}"
    name = "api:dashie:stream:feed"
    requires_auth = True

    async def get(self, request: web.Request, feed_id: str) -> web.StreamResponse:
        """Subscribe to a feed's shared MJPEG stream."""
        from .const import DOMAIN
        hass: HomeAssistant = request.app["hass"]

        # Look up feed definition
        registry = hass.data[DOMAIN].get("feed_registry")
        if not registry:
            return web.json_response({"error": "Feed registry not initialized"}, status=503)

        feed = registry.get_feed(feed_id)
        if not feed:
            return web.json_response({"error": f"Feed '{feed_id}' not found"}, status=404)

        multiplexer: StreamMultiplexer = hass.data[DOMAIN].get("stream_multiplexer")
        if not multiplexer:
            return web.json_response({"error": "Stream multiplexer not initialized"}, status=503)

        # Subscribe to shared stream
        sub_id, queue, stream = await multiplexer.subscribe(feed_id, feed)

        # Prepare MJPEG response
        response = web.StreamResponse(
            headers={
                "Content-Type": "multipart/x-mixed-replace; boundary=frame",
                "Cache-Control": "no-cache, no-store",
                "Connection": "keep-alive",
            }
        )
        await response.prepare(request)

        boundary = b"--frame\r\n"
        try:
            while True:
                frame_data = await queue.get()
                if frame_data is None:
                    break

                frame = (
                    boundary
                    + b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame_data)}\r\n\r\n".encode()
                    + frame_data
                    + b"\r\n"
                )
                try:
                    # Timeout write to detect dead clients (device crash leaves
                    # TCP half-open; without timeout, subscriber lingers forever
                    # and prevents FFmpeg from stopping)
                    await asyncio.wait_for(response.write(frame), timeout=10.0)
                except asyncio.TimeoutError:
                    _LOGGER.info(
                        "Feed %s sub %d: write timed out (client likely crashed)",
                        feed_id, sub_id,
                    )
                    break
                except (ConnectionResetError, ConnectionAbortedError):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            multiplexer.unsubscribe(feed_id, sub_id)

        return response


def register_stream_multiplexer_views(hass: HomeAssistant) -> None:
    """Register stream multiplexer HTTP views."""
    hass.http.register_view(DashieFeedStreamView())
    _LOGGER.info("Registered Dashie feed stream multiplexer view")
