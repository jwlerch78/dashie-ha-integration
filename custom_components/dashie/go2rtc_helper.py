"""
go2rtc Detection and Management for Dashie Integration

Auto-detects go2rtc from various sources:
- Standalone go2rtc addon
- Frigate's built-in go2rtc
- Custom installations

Provides helpful prompts if go2rtc is not found.
"""

import logging
import aiohttp
from typing import Optional
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Common go2rtc endpoints to check
GO2RTC_ENDPOINTS = [
    ("http://localhost:1984", "standalone"),
    ("http://127.0.0.1:1984", "standalone"),
    ("http://ccab4aaf-go2rtc:1984", "addon"),  # go2rtc addon Docker hostname
    ("http://addon_ccab4aaf_go2rtc:1984", "addon"),  # Alternative addon hostname
    ("http://ccab4aaf-frigate:1984", "frigate"),  # Frigate addon
    ("http://frigate:1984", "frigate"),  # Frigate container
    ("http://homeassistant.local:1984", "custom"),
]


async def detect_go2rtc(hass: HomeAssistant, custom_url: Optional[str] = None) -> Optional[str]:
    """
    Auto-detect go2rtc endpoint.

    Args:
        hass: Home Assistant instance
        custom_url: Optional custom go2rtc URL to try first

    Returns:
        go2rtc base URL if found, None otherwise
    """
    # Build endpoint list with custom URL first
    endpoints = []
    if custom_url:
        endpoints.append((custom_url, "custom"))
    endpoints.extend(GO2RTC_ENDPOINTS)

    async with aiohttp.ClientSession() as session:
        for url, source in endpoints:
            try:
                async with session.get(
                    f"{url}/api",
                    timeout=aiohttp.ClientTimeout(total=2)
                ) as response:
                    if response.status == 200:
                        _LOGGER.info("Found go2rtc at %s (source: %s)", url, source)
                        return url
            except Exception:
                continue

    _LOGGER.warning("go2rtc not found at any known endpoint")
    return None


async def check_go2rtc_stream(url: str, stream_name: str) -> bool:
    """
    Check if a specific stream exists in go2rtc.

    Args:
        url: go2rtc base URL
        stream_name: Name of the stream to check

    Returns:
        True if stream exists
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{url}/api/streams",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    streams = await response.json()
                    return stream_name in streams
    except Exception as e:
        _LOGGER.error("Failed to check go2rtc streams: %s", e)

    return False


async def get_go2rtc_streams(url: str) -> list:
    """
    Get list of all streams from go2rtc.

    Args:
        url: go2rtc base URL

    Returns:
        List of stream names
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{url}/api/streams",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    streams = await response.json()
                    return list(streams.keys())
    except Exception as e:
        _LOGGER.error("Failed to get go2rtc streams: %s", e)

    return []


def notify_go2rtc_missing(hass: HomeAssistant) -> None:
    """
    Create a notification prompting user to install go2rtc.
    """
    hass.components.persistent_notification.async_create(
        title="📹 go2rtc Recommended for Dashie Camera Card",
        message="""
The Dashie Camera Card works best with **go2rtc** for adaptive streaming.

**Benefits of go2rtc:**
- HLS streaming (works on all tablets)
- Automatic codec transcoding
- Lower latency than MJPEG
- Better performance with multiple cameras

**To install go2rtc:**

1. Go to [Settings → Add-ons](/hassio/store)
2. Search for "go2rtc"
3. Click Install, then Start

**Or if you use Frigate:**
go2rtc is already included! The Dashie card will auto-detect it.

**Without go2rtc:**
The camera card will fall back to MJPEG streaming, which works but uses more bandwidth and has lower quality.

[Install go2rtc Add-on](/hassio/addon/go2rtc/info)
        """.strip(),
        notification_id="dashie_go2rtc_missing",
    )


def notify_go2rtc_found(hass: HomeAssistant, url: str, source: str) -> None:
    """
    Create a notification confirming go2rtc was found.
    """
    hass.components.persistent_notification.async_create(
        title="✅ go2rtc Detected",
        message=f"""
Dashie Camera Card found go2rtc at:
`{url}`

Source: {source}

Your cameras will use HLS streaming for the best tablet experience.
        """.strip(),
        notification_id="dashie_go2rtc_found",
    )


async def setup_go2rtc_detection(hass: HomeAssistant, custom_url: Optional[str] = None) -> Optional[str]:
    """
    Detect go2rtc and notify user appropriately.

    Args:
        hass: Home Assistant instance
        custom_url: Optional custom go2rtc URL

    Returns:
        go2rtc URL if found
    """
    url = await detect_go2rtc(hass, custom_url)

    if url:
        # Found go2rtc - store in hass.data for card to use
        hass.data.setdefault("dashie", {})["go2rtc_url"] = url
        _LOGGER.info("go2rtc available at %s", url)
    else:
        # Not found - prompt user to install
        notify_go2rtc_missing(hass)
        _LOGGER.warning("go2rtc not found - camera card will use MJPEG fallback")

    return url


# Service handlers for go2rtc management

async def async_check_go2rtc_service(hass: HomeAssistant, call) -> dict:
    """
    Service to check go2rtc availability.

    Returns status and URL if found.
    """
    custom_url = call.data.get("url")
    url = await detect_go2rtc(hass, custom_url)

    if url:
        streams = await get_go2rtc_streams(url)
        return {
            "available": True,
            "url": url,
            "streams": streams,
            "stream_count": len(streams),
        }

    return {
        "available": False,
        "url": None,
        "streams": [],
        "stream_count": 0,
        "message": "go2rtc not found. Install from Settings → Add-ons → go2rtc",
    }


def build_camgrid_command(
    cameras: list[str],
    grid: str = "auto",
    fps: int = 10,
    quality: int = 30,
) -> str:
    """
    Build an FFmpeg exec command for camgrid composite grid.

    Args:
        cameras: List of camera entity IDs
        grid: Grid layout ('2x1', '2x2', etc.) or 'auto'
        fps: Frames per second
        quality: CRF quality value (0-51, higher = lower quality)

    Returns:
        exec:ffmpeg command string for go2rtc
    """
    num_cameras = len(cameras)

    # Auto-detect grid if not specified
    if grid == "auto":
        if num_cameras <= 2:
            cols, rows = num_cameras, 1
        elif num_cameras <= 4:
            cols, rows = 2, 2
        elif num_cameras <= 6:
            cols, rows = 3, 2
        else:
            cols = 3
            rows = (num_cameras + 2) // 3
    else:
        parts = grid.split("x")
        cols, rows = int(parts[0]), int(parts[1])

    # Calculate cell dimensions for 480p-ish output
    cell_width = 854 // cols
    cell_height = 480 // rows
    gop_size = fps * 2

    # Build inputs and filter_complex
    inputs = []
    scale_filters = []
    labels = []

    for i, cam in enumerate(cameras):
        inputs.append(f"-thread_queue_size 64 -rtsp_transport tcp -i rtsp://127.0.0.1:8554/{cam}")
        scale_filters.append(f"[{i}:v]fps={fps},scale={cell_width}:{cell_height},setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"v{i}")

    # Build stack filters
    stack_filters = []

    if rows == 1:
        # Single row - just hstack
        hstack_inputs = "".join(f"[{l}]" for l in labels[:cols])
        stack_filters.append(f"{hstack_inputs}hstack=inputs={cols}[v]")
    else:
        # Multiple rows - hstack each row, then vstack
        row_outputs = []
        for r in range(rows):
            row_labels = labels[r * cols:(r + 1) * cols]
            if len(row_labels) < cols:
                # Pad with black if fewer cameras than grid slots
                for i in range(len(row_labels), cols):
                    idx = len(cameras) + i
                    scale_filters.append(f"nullsrc=s={cell_width}x{cell_height}:d=1,loop=-1:1[v{idx}]")
                    row_labels.append(f"v{idx}")

            row_inputs = "".join(f"[{l}]" for l in row_labels)
            row_output = f"row{r}"
            stack_filters.append(f"{row_inputs}hstack=inputs={cols}[{row_output}]")
            row_outputs.append(row_output)

        # vstack all rows
        vstack_inputs = "".join(f"[{r}]" for r in row_outputs)
        stack_filters.append(f"{vstack_inputs}vstack=inputs={rows}[v]")

    filter_complex = ";".join(scale_filters + stack_filters)

    # Build full command
    command = (
        f"exec:ffmpeg -hide_banner -fflags nobuffer -flags low_delay "
        f"{' '.join(inputs)} "
        f"-filter_complex '{filter_complex}' "
        f"-map '[v]' -an "
        f"-c:v libx264 -preset superfast -tune zerolatency -crf {quality} -profile:v baseline "
        f"-r {fps} -g {gop_size} -f mpegts pipe:1"
    )

    return command


async def provision_camgrid_stream(
    go2rtc_url: str,
    cameras: list[str],
    grid: str = "auto",
    fps: int = 10,
    quality: int = 30,
    stream_name: Optional[str] = None,
) -> dict:
    """
    Provision a camgrid composite stream in go2rtc.

    This runs from HA (localhost), so it's a trusted producer and can create exec: sources.

    Args:
        go2rtc_url: Base URL of go2rtc server
        cameras: List of camera entity IDs
        grid: Grid layout ('2x1', '2x2', etc.) or 'auto'
        fps: Frames per second
        quality: CRF quality value
        stream_name: Optional custom stream name

    Returns:
        dict with 'success', 'stream_name', and 'url' keys
    """
    # Generate stream name from camera list if not provided
    if not stream_name:
        cam_hash = "_".join(c.replace("camera.", "") for c in cameras)
        stream_name = f"dashie_camgrid_{cam_hash}"

    # Check if stream already exists
    if await check_go2rtc_stream(go2rtc_url, stream_name):
        _LOGGER.info("Birdseye stream already exists: %s", stream_name)
        return {
            "success": True,
            "stream_name": stream_name,
            "url": f"{go2rtc_url}/api/stream.mp4?src={stream_name}",
            "already_exists": True,
        }

    # Build the FFmpeg command
    exec_command = build_camgrid_command(cameras, grid, fps, quality)
    _LOGGER.info("Provisioning camgrid stream %s with command: %s", stream_name, exec_command)

    # Provision via go2rtc API (from localhost = trusted producer)
    try:
        async with aiohttp.ClientSession() as session:
            # URL encode the source
            import urllib.parse
            api_url = (
                f"{go2rtc_url}/api/streams"
                f"?name={urllib.parse.quote(stream_name)}"
                f"&src={urllib.parse.quote(exec_command)}"
            )

            async with session.put(
                api_url,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status in (200, 201):
                    _LOGGER.info("Successfully provisioned camgrid stream: %s", stream_name)
                    return {
                        "success": True,
                        "stream_name": stream_name,
                        "url": f"{go2rtc_url}/api/stream.mp4?src={stream_name}",
                        "already_exists": False,
                    }
                else:
                    error_text = await response.text()
                    _LOGGER.error("Failed to provision stream: %s - %s", response.status, error_text)
                    return {
                        "success": False,
                        "error": f"go2rtc returned {response.status}: {error_text}",
                    }

    except Exception as e:
        _LOGGER.error("Failed to provision camgrid stream: %s", e)
        return {
            "success": False,
            "error": str(e),
        }
