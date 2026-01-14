"""Camera entity for Dashie Lite integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_DEVICE_ID
from .coordinator import DashieCoordinator
from .entity import DashieEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie Lite camera."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]

    entities = [
        DashieCamera(coordinator, device_id),
    ]

    async_add_entities(entities)


class DashieCamera(DashieEntity, Camera):
    """Camera entity for Dashie Lite device."""

    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_translation_key = "camera"
    _attr_frame_interval = 10  # Seconds between thumbnail updates

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the camera."""
        DashieEntity.__init__(self, coordinator, device_id)
        Camera.__init__(self)
        self._attr_unique_id = f"{device_id}_camera"
        self._attr_name = "Camera"
        self._attr_is_streaming = False
        self._stream_url: str | None = None
        self._last_image: bytes | None = None

    @property
    def is_on(self) -> bool:
        """Return true if camera is streaming."""
        if self.coordinator.data:
            # Check RTSP status from device info or last known state
            return self._attr_is_streaming
        return False

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera."""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{self.coordinator.base_url}/?cmd=getCamshot"
                if self.coordinator.password:
                    url += f"&password={self.coordinator.password}"

                async with session.get(url) as response:
                    if response.status == 200:
                        content_type = response.headers.get("Content-Type", "")
                        if "image" in content_type:
                            self._last_image = await response.read()
                            return self._last_image
                        # API returned JSON error instead of image
                        _LOGGER.debug("Camera returned non-image response")
                        return self._last_image  # Return cached image if available
                    _LOGGER.warning("Failed to get camera image: %s", response.status)
                    return self._last_image
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout getting camera image from %s", self.coordinator.host)
            return self._last_image
        except aiohttp.ClientError as err:
            _LOGGER.warning("Error getting camera image: %s", err)
            return self._last_image

    async def stream_source(self) -> str | None:
        """Return the stream source URL."""
        if self._stream_url:
            return self._stream_url

        # Try to get RTSP stream URL from device
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{self.coordinator.base_url}/?cmd=getRtspStatus"
                if self.coordinator.password:
                    url += f"&password={self.coordinator.password}"

                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("isStreaming"):
                            self._stream_url = data.get("streamUrl")
                            self._attr_is_streaming = True
                            return self._stream_url
        except Exception as err:
            _LOGGER.debug("Could not get RTSP status: %s", err)

        return None

    async def async_turn_on(self) -> None:
        """Turn on the camera (start RTSP stream)."""
        success = await self.coordinator.send_command("startRtspStream")
        if success:
            self._attr_is_streaming = True
            # Get the stream URL
            await self.stream_source()
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Turn off the camera (stop RTSP stream)."""
        await self.coordinator.send_command("stopRtspStream")
        self._attr_is_streaming = False
        self._stream_url = None
        await self.coordinator.async_request_refresh()
