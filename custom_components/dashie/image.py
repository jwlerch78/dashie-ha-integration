"""Screenshot image entity for Dashie integration."""
from __future__ import annotations

import logging
from datetime import datetime

import aiohttp

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_DEVICE_ID, API_GET_SCREENSHOT
from .coordinator import DashieCoordinator
from .entity import DashieEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie screenshot image entity."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]

    async_add_entities([DashieScreenshot(coordinator, device_id)])


class DashieScreenshot(DashieEntity, ImageEntity):
    """Image entity that shows what's currently displayed on the Dashie tablet."""

    _attr_translation_key = "screenshot"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the screenshot entity."""
        DashieEntity.__init__(self, coordinator, device_id)
        ImageEntity.__init__(self, coordinator.hass)
        self._attr_unique_id = f"{device_id}_screenshot"
        self._attr_name = "Screenshot"
        self._cached_image: bytes | None = None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success

    async def async_image(self) -> bytes | None:
        """Return a screenshot from the device."""
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{self.coordinator.base_url}/?cmd={API_GET_SCREENSHOT}"
                if self.coordinator.password:
                    url += f"&password={self.coordinator.password}"

                async with session.get(url) as response:
                    if response.status == 200:
                        content_type = response.headers.get("Content-Type", "")
                        if "image" in content_type:
                            self._cached_image = await response.read()
                            self._attr_image_last_updated = datetime.now()
                            return self._cached_image

                    _LOGGER.debug("Screenshot request failed: status=%s", response.status)
                    return self._cached_image
        except aiohttp.ClientError as err:
            _LOGGER.debug("Error getting screenshot: %s", err)
            return self._cached_image
