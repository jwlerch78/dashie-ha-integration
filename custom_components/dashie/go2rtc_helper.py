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
