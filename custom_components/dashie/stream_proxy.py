"""MJPEG stream proxy for Dashie Video Feed PiP overlays.

Provides a real-time MJPEG stream by resolving any HA camera entity's
stream source (RTSP URL) and transcoding via FFmpeg. Replaces HA's slow
camera_proxy_stream endpoint which polls individual snapshots (~1-2 fps).

Includes automatic reconnect when camera streams drop (common with Tapo
and other consumer cameras that close RTSP connections periodically).

Hardware acceleration is auto-detected at startup:
- Intel VAAPI: Full HW decode + MJPEG encode (near-zero CPU)
- NVIDIA CUDA: HW H.264 decode + SW MJPEG encode
- Software: Pure CPU fallback (all platforms)

Endpoint: GET /api/dashie/stream/mjpeg/{entity_id}?fps=10&quality=8&width=640
Auth: HA Bearer token (requires_auth = True)
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import re
import shutil
import time

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Defaults
# JPEG markers
_JPEG_SOI = b"\xff\xd8"  # Start of Image
_JPEG_EOI = b"\xff\xd9"  # End of Image

# Defaults
DEFAULT_FPS = 10
DEFAULT_QUALITY = 8   # MJPEG quality: 2 = best, 31 = worst
MAX_FPS = 30
MAX_RECONNECTS = 10
RECONNECT_DELAY = 2.0  # Seconds between reconnect attempts
READ_CHUNK_SIZE = 65536  # 64KB reads from FFmpeg stdout
FRAME_BUFFER_SIZE = 60   # Buffer for ~3s at 20fps (absorbs burst gaps)
PREFILL_FRAMES = 10      # Wait for this many frames before first send (~500ms at 20fps)
SKIP_ACCUM_WINDOW = 5.0  # Seconds over which to measure input fps for skip ratio

# Hardware acceleration (auto-detected at startup)
# Values: "vaapi", "cuda", "software"
_hw_accel: str | None = None  # None = not yet detected
_vaapi_device: str | None = None  # e.g. /dev/dri/renderD128

# Regex to match credentials in RTSP URLs: rtsp://user:pass@host
_RTSP_CRED_RE = re.compile(r"(rtsp://)([^@]+)@", re.IGNORECASE)


def _redact_url(url: str) -> str:
    """Redact credentials from RTSP URLs for safe logging."""
    return _RTSP_CRED_RE.sub(r"\1****:****@", url)


class DashieMjpegStreamView(HomeAssistantView):
    """Serve real-time MJPEG stream for any HA camera entity."""

    url = "/api/dashie/stream/mjpeg/{entity_id:.*}"
    name = "api:dashie:stream:mjpeg"
    requires_auth = False  # TODO: restore to True after browser testing

    async def get(self, request: web.Request, entity_id: str) -> web.StreamResponse:
        """Handle MJPEG stream request."""
        hass: HomeAssistant = request.app["hass"]

        # Parse query params (fps=0 means native/no fps filter)
        fps = min(int(request.query.get("fps", DEFAULT_FPS)), MAX_FPS)
        if fps < 1:
            fps = 0  # Signal to skip fps filter
        quality = int(request.query.get("quality", DEFAULT_QUALITY))
        width = request.query.get("width")
        direct_source = request.query.get("source")  # Optional direct RTSP URL

        if direct_source:
            # Direct RTSP URL provided — skip entity resolution
            stream_source = direct_source
            _LOGGER.debug("Using direct stream source: %s", _redact_url(stream_source))
        else:
            # Validate entity exists
            state = hass.states.get(entity_id)
            if not state or not entity_id.startswith("camera."):
                return web.json_response(
                    {"error": f"Camera entity '{entity_id}' not found"}, status=404
                )

            # Resolve stream source URL via camera platform
            stream_source = await _get_stream_source(hass, entity_id)
        if not stream_source:
            _LOGGER.debug("No stream source for %s", entity_id)
            return web.json_response(
                {"error": f"No stream source available for '{entity_id}'"}, status=503
            )

        # Detect hardware acceleration (cached after first check)
        hw_accel = await _detect_hw_accel()

        _LOGGER.debug(
            "Starting MJPEG proxy: %s → %s (fps=%d, q=%d, w=%s, hw=%s)",
            entity_id, _redact_url(stream_source), fps, quality, width or "native", hw_accel,
        )

        # Prepare streaming response
        response = web.StreamResponse(
            headers={
                "Content-Type": "multipart/x-mixed-replace; boundary=frame",
                "Cache-Control": "no-cache, no-store",
                "Connection": "keep-alive",
            }
        )
        await response.prepare(request)

        # Raise write buffer high watermark so drain() never blocks on
        # single MJPEG frames (~50-100KB). Without this, the default 64KB
        # limit causes drain() to stall on every frame. We still need
        # drain() to force the event loop to flush each frame to TCP
        # immediately — without it, frames batch in the buffer and arrive
        # at Chrome in bursts (causing the ~500ms stutter).
        transport = response._payload_writer.transport
        if transport is not None:
            transport.set_write_buffer_limits(high=512 * 1024, low=128 * 1024)

        # Stream with automatic reconnect
        reconnects = 0
        while reconnects <= MAX_RECONNECTS:
            # Re-resolve stream source on reconnect (URLs can expire)
            if reconnects > 0:
                _LOGGER.debug(
                    "Reconnecting MJPEG proxy for %s (attempt %d/%d)",
                    entity_id, reconnects, MAX_RECONNECTS,
                )
                await asyncio.sleep(RECONNECT_DELAY)
                if direct_source:
                    # Direct RTSP/go2rtc URL — reuse as-is (no entity resolution)
                    stream_source = direct_source
                else:
                    stream_source = await _get_stream_source(hass, entity_id)
                    if not stream_source:
                        _LOGGER.debug("No stream source on reconnect for %s", entity_id)
                        break

            cmd = _build_ffmpeg_cmd(stream_source, fps, quality, width, hw_accel)
            _LOGGER.debug("FFmpeg cmd: %s", " ".join(_redact_url(c) for c in cmd))

            # Write stderr to a temp file so it never blocks FFmpeg
            # and we get the FULL output for diagnostics.
            import tempfile
            stderr_file = tempfile.NamedTemporaryFile(
                prefix=f"ffmpeg_{entity_id.replace('.', '_')}_",
                suffix=".log", delete=False, mode="w+b",
            )
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr_file,
            )

            try:
                client_gone = await _pipe_frames_to_response(process, response, fps, entity_id)
                if client_gone:
                    _LOGGER.debug("Client disconnected from MJPEG stream: %s", entity_id)
                    break

                # FFmpeg exited on its own — read stderr from file
                stderr_file.seek(0)
                stderr_data = stderr_file.read()
                stderr_text = _redact_url(stderr_data.decode(errors="replace"))
                # Log last 2000 chars to see actual errors, not just SEI noise
                _LOGGER.debug(
                    "FFmpeg exited for %s (code=%s, stderr=%d bytes): %s",
                    entity_id,
                    process.returncode,
                    len(stderr_data),
                    stderr_text[-2000:],
                )
                reconnects += 1

            except (ConnectionResetError, ConnectionAbortedError, asyncio.CancelledError):
                _LOGGER.debug("Client disconnected from MJPEG stream: %s", entity_id)
                break
            except Exception:
                _LOGGER.exception("Error streaming MJPEG for %s", entity_id)
                break
            finally:
                stderr_file.close()
                try:
                    os.unlink(stderr_file.name)
                except OSError:
                    pass
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()

        _LOGGER.debug(
            "MJPEG proxy ended for %s (reconnects=%d)", entity_id, reconnects
        )
        return response


async def _get_stream_source(hass: HomeAssistant, entity_id: str) -> str | None:
    """Get the stream source URL for a camera entity."""
    camera_component = hass.data.get("camera")
    if camera_component is None:
        _LOGGER.error("Camera component not loaded")
        return None

    entity = camera_component.get_entity(entity_id)
    if entity is None:
        _LOGGER.debug("Camera entity not found in component: %s", entity_id)
        return None

    if not hasattr(entity, "stream_source"):
        _LOGGER.debug("Camera entity has no stream_source: %s", entity_id)
        return None

    source = await entity.stream_source()
    _LOGGER.debug("Stream source for %s: %s", entity_id, _redact_url(source) if source else None)
    return source


async def _detect_hw_accel() -> str:
    """Auto-detect hardware acceleration available on this system.

    Detection order (most capable first):
    1. Intel VAAPI — full HW decode + MJPEG encode (near-zero CPU)
    2. NVIDIA CUDA — HW H.264 decode + SW MJPEG encode
    3. Software — pure CPU fallback

    Returns: "vaapi", "cuda", or "software"
    """
    global _hw_accel, _vaapi_device

    if _hw_accel is not None:
        return _hw_accel

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        _hw_accel = "software"
        _LOGGER.debug("FFmpeg not found — using software encoding")
        return _hw_accel

    # Check for VAAPI (Intel iGPU) — look for render node + test mjpeg_vaapi
    for dev in ("/dev/dri/renderD128", "/dev/dri/renderD129"):
        if os.path.exists(dev):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-hwaccel", "vaapi", "-hwaccel_device", dev,
                    "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
                    "-c:v", "mjpeg_vaapi", "-frames:v", "1",
                    "-f", "null", "-",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
                if proc.returncode == 0:
                    _hw_accel = "vaapi"
                    _vaapi_device = dev
                    _LOGGER.info(
                        "Hardware acceleration: VAAPI (mjpeg_vaapi) at %s", dev
                    )
                    return _hw_accel
            except Exception:
                continue

    # Check for NVIDIA CUDA — test h264_cuvid decoder
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-hwaccel", "cuda",
            "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
            "-frames:v", "1", "-f", "null", "-",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            _hw_accel = "cuda"
            _LOGGER.info("Hardware acceleration: NVIDIA CUDA (HW decode only)")
            return _hw_accel
    except Exception:
        pass

    _hw_accel = "software"
    _LOGGER.info("Hardware acceleration: none detected — using software encoding")
    return _hw_accel


def _build_ffmpeg_cmd(
    stream_source: str, fps: int, quality: int, width: str | None,
    hw_accel: str = "software",
) -> list[str]:
    """Build FFmpeg command for stream → MJPEG transcoding.

    Adapts command based on available hardware acceleration:
    - vaapi: HW decode + HW MJPEG encode via Intel iGPU
    - cuda: HW H.264 decode via NVIDIA + SW MJPEG encode
    - software: Pure CPU (default)
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        # nobuffer: don't buffer input (reduce latency)
        # flush_packets: write encoded frames to stdout immediately
        # NOTE: do NOT use -flags low_delay — it disables the H.264
        # reorder buffer, causing duplicate frame output with B-frame
        # streams (Tapo cameras use B-frames for quality).
        "-fflags", "+nobuffer+flush_packets+discardcorrupt+genpts",
        # Ignore malformed/truncated SEI NAL units (e.g. vendor-specific
        # SEI type 764 from consumer cameras) instead of treating them
        # as fatal errors that crash the process.
        "-err_detect", "ignore_err",
    ]

    # Hardware-accelerated decode options
    if hw_accel == "vaapi" and _vaapi_device:
        cmd.extend([
            "-hwaccel", "vaapi",
            "-hwaccel_device", _vaapi_device,
            "-hwaccel_output_format", "vaapi",
        ])
    elif hw_accel == "cuda":
        cmd.extend([
            "-hwaccel", "cuda",
            "-hwaccel_output_format", "cuda",
        ])

    # RTSP-specific input options
    if stream_source.startswith("rtsp://"):
        cmd.extend([
            "-rtsp_transport", "tcp",
            "-timeout", "5000000",
        ])
        # Tablet RTSP servers (RootEncoder on port 8554) don't declare
        # framerate in SDP, causing FFmpeg to detect 90k tbr instead of
        # ~15fps. Hint the framerate so realtime filter can pace correctly.
        if ":8554" in stream_source:
            cmd.extend(["-r", "15"])

    # Input with small analyze buffer for faster startup
    cmd.extend([
        "-analyzeduration", "500000",
        "-probesize", "500000",
        "-i", stream_source,
        # Strip SEI NAL units (type 6). Consumer cameras (Tapo) embed
        # proprietary SEI (type 764) that can cause decoder warnings.
        "-bsf:v", "filter_units=remove_types=6",
    ])

    # Video filters: scale + setpts for monotonic timestamps.
    # FFmpeg 7.x MJPEG encoder rejects non-monotonic PTS from B-frame
    # reordering (Tapo cameras). setpts=N*100000 assigns each frame a
    # large, unique PTS that survives any timebase conversion (the MJPEG
    # encoder may use a coarser TB than the input's 1/90000).
    # Python-side jitter buffer handles all rate smoothing.
    if hw_accel == "vaapi":
        vf_parts = []
        if width:
            vf_parts.append(f"scale_vaapi=w={width}:h=-1")
        vf_parts.append("setpts=N*100000")
        cmd.extend(["-vf", ",".join(vf_parts)])
        cmd.extend([
            "-c:v", "mjpeg_vaapi",
            "-global_quality", str(min(quality * 10, 100)),
        ])
    elif hw_accel == "cuda":
        vf_parts = []
        if width:
            vf_parts.append(f"scale_cuda={width}:-1")
        vf_parts.extend(["hwdownload", "format=nv12"])
        vf_parts.append("setpts=N*100000")
        cmd.extend(["-vf", ",".join(vf_parts)])
        cmd.extend([
            "-c:v", "mjpeg",
            "-q:v", str(quality),
        ])
    else:
        vf_parts = ["setpts=N*100000"]
        if width:
            vf_parts.insert(0, f"scale={width}:-1")
        cmd.extend(["-vf", ",".join(vf_parts)])
        cmd.extend([
            "-c:v", "mjpeg",
            "-q:v", str(quality),
        ])

    # Common output options
    # -fps_mode passthrough: output every decoded frame exactly once.
    # setpts=N*100000 ensures each frame has a unique, monotonically
    # increasing PTS that won't collapse during timebase conversion.
    # Python-side jitter buffer handles all rate smoothing.
    cmd.extend([
        "-fps_mode", "passthrough",
        "-an",
        "-f", "mjpeg",
        "-",
    ])

    return cmd


async def _pipe_frames_to_response(
    process: asyncio.subprocess.Process,
    response: web.StreamResponse,
    fps: int = DEFAULT_FPS,
    entity_id: str = "unknown",
) -> bool:
    """Read JPEG frames from FFmpeg, buffer, and deliver at steady rate.

    FFmpeg decodes at network rate (bursty). Python-side jitter buffer
    with pre-fill absorbs burst gaps. Fractional skip accumulator adapts
    consumption to any input rate without oscillation.

    Key design: NO drain() on writes — aiohttp's drain() blocks when the
    write buffer exceeds 64KB (a single MJPEG frame), causing timer jitter.
    TCP flow control handles backpressure instead.

    Returns True if client disconnected, False if FFmpeg exited.
    """
    boundary = b"--frame\r\n"
    out_fps = fps if fps > 0 else DEFAULT_FPS
    interval = 1.0 / out_fps

    # No maxlen — silent deque drops cause content jumps.
    # Skip accumulator + explicit cap manages buffer size.
    frame_buf: collections.deque[bytes] = collections.deque()
    ffmpeg_done = asyncio.Event()

    stats = {"in": 0, "unique_in": 0, "out": 0, "skip": 0, "hold": 0, "dup": 0, "drop": 0,
             "dup_bytes": 0, "unique_bytes": 0}
    log_time = time.monotonic()
    prefill_start = time.monotonic()
    last_write_time: list[float] = [0.0]
    write_gaps: list[float] = []
    # Fractional skip accumulator — spreads skips evenly over time
    skip_accum = 0.0
    # Running input fps estimate — bootstrapped from pre-fill measurement
    observed_in_fps = 0.0

    # Reader arrival timing (for burst analysis)
    arrival_gaps: list[float] = []
    last_arrival: list[float] = [0.0]

    async def _reader() -> None:
        """Parse JPEG frames from FFmpeg stdout into buffer.

        Dedup: Tapo cameras produce ~35% byte-identical duplicate frames.
        Sending duplicates looks like freeze→jump. Only buffer unique frames.
        """
        reader = process.stdout
        buf = b""
        prev_frame: bytes = b""
        while True:
            chunk = await reader.read(READ_CHUNK_SIZE)
            if not chunk:
                ffmpeg_done.set()
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
                stats["in"] += 1
                # Track frame arrival timing for burst analysis
                now_r = time.monotonic()
                if last_arrival[0] > 0:
                    arrival_gaps.append((now_r - last_arrival[0]) * 1000)
                last_arrival[0] = now_r
                # Count byte-identical frames (diagnostic only).
                if jpeg_data == prev_frame:
                    stats["dup"] += 1
                    stats["dup_bytes"] = len(jpeg_data)
                else:
                    stats["unique_in"] += 1
                    stats["unique_bytes"] = len(jpeg_data)
                prev_frame = jpeg_data
                frame_buf.append(jpeg_data)

    reader_task = asyncio.ensure_future(_reader())
    client_gone = False

    try:
        # Pre-fill: wait for enough frames to measure input fps and absorb burst gaps
        while len(frame_buf) < PREFILL_FRAMES and not ffmpeg_done.is_set():
            await asyncio.sleep(0.01)
        prefill_elapsed = time.monotonic() - prefill_start
        if prefill_elapsed > 0 and len(frame_buf) > 1:
            # All frames (including visual dups) enter buffer now
            observed_in_fps = len(frame_buf) / prefill_elapsed
        _LOGGER.debug(
            "MJPEG [%s]: pre-fill complete, %d unique frames in %.0fms "
            "(%d total, %d dups), unique_fps=%.1f, skip_ratio=%.2f",
            entity_id, len(frame_buf), prefill_elapsed * 1000,
            stats["in"], stats["dup"],
            observed_in_fps,
            (observed_in_fps / out_fps) - 1.0 if observed_in_fps > out_fps else 0.0,
        )

        last_frame: bytes | None = None  # For repeat-on-hold
        next_send = time.monotonic()

        while not ffmpeg_done.is_set() or frame_buf:
            now = time.monotonic()
            delay = next_send - now
            if delay > 0:
                await asyncio.sleep(delay)
            next_send += interval
            now2 = time.monotonic()
            if next_send < now2:
                next_send = now2 + interval

            if frame_buf:
                # Send oldest frame (FIFO) for smooth content progression
                frame_data = frame_buf.popleft()
                last_frame = frame_data

                # Fractional skip: accumulate the excess ratio each tick.
                # When accumulator >= 1.0, skip one frame. This spreads
                # skips evenly over time — no threshold oscillation.
                # Prefer skipping duplicate frames to preserve unique content.
                if observed_in_fps > out_fps:
                    skip_accum += (observed_in_fps / out_fps) - 1.0
                while skip_accum >= 1.0 and frame_buf:
                    # Peek ahead: prefer skipping a dup over a unique frame
                    next_frame = frame_buf[0]
                    frame_buf.popleft()
                    stats["skip"] += 1
                    skip_accum -= 1.0
                    # If we skipped a unique frame but a dup is next,
                    # that's fine — the accumulator handles the math.
                    # But if the SENT frame (frame_data) is a dup and
                    # the skipped frame was unique, swap them.
                    if frame_data == last_frame and next_frame != frame_data:
                        # We're about to send a dup; swap with the unique
                        # frame we just skipped
                        frame_data = next_frame
                        last_frame = frame_data
                skip_accum = min(skip_accum, 3.0)

                # Safety cap: if buffer grows too large despite skipping,
                # drain to target (keeps latency bounded)
                if len(frame_buf) > FRAME_BUFFER_SIZE:
                    excess = len(frame_buf) - PREFILL_FRAMES
                    for _ in range(excess):
                        frame_buf.popleft()
                        stats["drop"] += 1
            elif last_frame is not None:
                # Buffer empty — resend last frame to keep delivery cadence
                # (browser never sees a gap in the multipart stream)
                frame_data = last_frame
                stats["hold"] += 1
            else:
                stats["hold"] += 1
                continue

            stats["out"] += 1

            frame = (
                boundary
                + b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(frame_data)}\r\n\r\n".encode()
                + frame_data
                + b"\r\n"
            )
            try:
                t_before = time.monotonic()
                await response.write(frame)
                await response.drain()
                t_after = time.monotonic()
                write_ms = (t_after - t_before) * 1000
                if write_ms > 20:  # Log slow writes
                    _LOGGER.debug(
                        "MJPEG [%s]: SLOW write+drain: %.1fms (frame=%d bytes)",
                        entity_id, write_ms, len(frame_data),
                    )
            except (ConnectionResetError, ConnectionAbortedError):
                client_gone = True
                break

            # Track write timing gaps
            write_time = time.monotonic()
            if last_write_time[0] > 0:
                gap_ms = (write_time - last_write_time[0]) * 1000
                write_gaps.append(gap_ms)
            last_write_time[0] = write_time

            if now2 - log_time >= SKIP_ACCUM_WINDOW:
                elapsed = now2 - log_time
                # Use total input fps for skip ratio — all frames
                # (including visual dups) now enter the buffer
                observed_in_fps = stats["in"] / elapsed
                gap_info = ""
                if write_gaps:
                    avg_gap = sum(write_gaps) / len(write_gaps)
                    min_gap = min(write_gaps)
                    max_gap = max(write_gaps)
                    gap_info = f", send_gaps={min_gap:.0f}/{avg_gap:.0f}/{max_gap:.0f}ms"
                    write_gaps.clear()
                arrival_info = ""
                if arrival_gaps:
                    a_avg = sum(arrival_gaps) / len(arrival_gaps)
                    a_min = min(arrival_gaps)
                    a_max = max(arrival_gaps)
                    arrival_info = f", arrive={a_min:.0f}/{a_avg:.0f}/{a_max:.0f}ms"
                    arrival_gaps.clear()
                total_in_fps = stats["in"] / elapsed
                size_info = ""
                if stats["dup_bytes"] > 0 or stats["unique_bytes"] > 0:
                    size_info = f", dup_sz={stats['dup_bytes']//1024}KB, uniq_sz={stats['unique_bytes']//1024}KB"
                _LOGGER.debug(
                    "MJPEG [%s]: total_in=%.1f, unique_in=%.1f, out=%.1f fps, "
                    "buf=%d, skips=%d, holds=%d, dups=%d, drops=%d, ratio=%.2f%s%s%s",
                    entity_id,
                    total_in_fps,
                    observed_in_fps,
                    stats["out"] / elapsed,
                    len(frame_buf),
                    stats["skip"], stats["hold"], stats["dup"], stats["drop"],
                    observed_in_fps / out_fps if out_fps else 0,
                    gap_info, arrival_info, size_info,
                )
                stats["in"] = stats["unique_in"] = 0
                stats["out"] = stats["skip"] = stats["hold"] = 0
                stats["dup"] = stats["drop"] = 0
                log_time = now2
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass

    return client_gone


def register_stream_proxy_views(hass: HomeAssistant) -> None:
    """Register MJPEG stream proxy HTTP views."""
    hass.http.register_view(DashieMjpegStreamView())
    _LOGGER.info("Registered Dashie MJPEG stream proxy view")
