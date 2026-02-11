"""Camera entity for Dashie Lite integration."""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

import aiohttp
from PIL import Image

from homeassistant.components.camera import Camera, CameraEntityFeature, StreamType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    API_SET_BOOLEAN_SETTING,
    API_START_RTSP_STREAM,
    API_STOP_RTSP_STREAM,
    SETTING_RTSP_ENABLED,
)
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
    _attr_frontend_stream_type = StreamType.HLS  # Use HA stream component for HLS

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the camera."""
        DashieEntity.__init__(self, coordinator, device_id)
        Camera.__init__(self)
        self._attr_unique_id = f"{device_id}_camera"
        self._attr_name = "Camera"
        self._attr_is_streaming = False
        self._stream_url: str | None = None
        self._last_image: bytes | None = None

    def _handle_coordinator_update(self) -> None:
        """Sync streaming state from coordinator data before HA reads state.

        HA's Camera.state checks is_streaming (_attr_is_streaming) to determine
        the entity state. We must update _attr_is_streaming here — not as a
        side effect in a property getter — so the value is current when HA
        reads state on each coordinator poll.
        """
        if self.coordinator.data:
            # Both conditions must be true: the preference is enabled AND
            # the server is actually running. This prevents showing the camera
            # as active when rtspEnabled is false but the server hasn't fully
            # stopped yet, or during startup race conditions.
            rtsp_enabled = bool(self.coordinator.data.get("rtspEnabled"))
            rtsp_status = self.coordinator.data.get("rtsp_status", {})
            is_streaming = rtsp_enabled and bool(rtsp_status.get("isStreaming"))

            if is_streaming:
                self._attr_is_streaming = True
                if rtsp_status.get("streamUrl"):
                    self._stream_url = rtsp_status["streamUrl"]
            else:
                self._attr_is_streaming = False
                self._stream_url = None
                self._last_image = None

        super()._handle_coordinator_update()

    @property
    def is_on(self) -> bool:
        """Return true if camera is streaming."""
        return self._attr_is_streaming

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera.

        Returns None when RTSP is disabled so the camera card doesn't show
        a snapshot that implies the camera is active. The getCamshot API
        endpoint on the device remains available for direct use.

        Note: Images are rotated 180° and horizontally flipped to match the RTSP stream
        orientation. The RTSP stream uses OpenGL filters to un-mirror the front camera
        (Android v2.21.9B+). This ensures snapshots match the live stream appearance.
        """
        if not self.is_on:
            return None

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
                            image_data = await response.read()

                            # Fix orientation to match RTSP stream:
                            # 1. Rotate 180° (upside down fix)
                            # 2. Horizontal flip (un-mirror front camera)
                            try:
                                image = Image.open(io.BytesIO(image_data))
                                rotated = image.rotate(180, expand=True)
                                flipped = rotated.transpose(Image.FLIP_LEFT_RIGHT)

                                # Convert back to JPEG bytes
                                output = io.BytesIO()
                                flipped.save(output, format="JPEG", quality=85)
                                self._last_image = output.getvalue()
                                return self._last_image
                            except Exception as err:
                                _LOGGER.warning("Failed to rotate image: %s (returning original)", err)
                                self._last_image = image_data
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
        """Return the stream source URL.

        Returns the RTSP URL whenever the device reports it is streaming,
        regardless of the cached _attr_is_streaming state. HA and go2rtc
        call this to get the URL for WebRTC/HLS — blocking it based on
        stale cached state causes the preview to fail on stream startup.
        """
        # Use cached URL if available
        if self._stream_url:
            _LOGGER.debug("stream_source returning cached URL: %s", self._stream_url)
            return self._stream_url

        # Try coordinator data (updated every 5s)
        if self.coordinator.data:
            rtsp_status = self.coordinator.data.get("rtsp_status", {})
            if rtsp_status.get("isStreaming") and rtsp_status.get("streamUrl"):
                self._stream_url = rtsp_status["streamUrl"]
                _LOGGER.debug("stream_source returning URL from coordinator: %s", self._stream_url)
                return self._stream_url

        # Fallback: fetch directly from device (for immediate response)
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
                            _LOGGER.debug("stream_source returning URL from device API: %s", self._stream_url)
                            return self._stream_url
        except Exception as err:
            _LOGGER.debug("Could not get RTSP status: %s", err)

        _LOGGER.debug("stream_source returning None (no stream available)")
        return None

    async def async_turn_on(self) -> None:
        """Turn on the camera (start RTSP stream).

        Sends two commands:
        1. setBooleanSetting to persist rtspEnabled preference (survives reboot)
        2. startRtspStream to immediately start the server (synchronous on device)

        Does NOT optimistically set _attr_is_streaming — the coordinator poll
        will detect isStreaming=True once the server is ready.

        Also clears HA's internal stream reference to force creation of a fresh
        stream worker on next access. This fixes issues where HA's stream component
        gets stuck in an error state after disconnects or HA reboots.
        """
        # Clear any cached stream to force HA to create a fresh one
        # This fixes stream component getting stuck after errors or HA reboot
        if hasattr(self, "_stream") and self._stream is not None:
            _LOGGER.debug("Clearing cached stream to force fresh connection")
            try:
                await self._stream.stop()
            except Exception:
                pass
            self._stream = None

        # Clear cached URL so stream_source() fetches fresh
        self._stream_url = None

        # Persist the preference
        await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_RTSP_ENABLED, value="true"
        )
        self.coordinator.update_local_data(rtspEnabled=True)
        # Start the server immediately
        await self.coordinator.send_command(API_START_RTSP_STREAM)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Turn off the camera (stop RTSP stream)."""
        # Persist the preference
        await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_RTSP_ENABLED, value="false"
        )
        # Stop the server immediately
        await self.coordinator.send_command(API_STOP_RTSP_STREAM)
        self._attr_is_streaming = False
        self._stream_url = None
        self.coordinator.update_local_data(rtspEnabled=False)
        await self.coordinator.async_request_refresh()
