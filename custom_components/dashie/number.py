"""Number entities for Dashie Lite integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    API_SET_BRIGHTNESS,
    API_SET_VOLUME,
)
from .coordinator import DashieCoordinator
from .entity import DashieEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie Lite number entities."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]

    entities = [
        DashieBrightnessNumber(coordinator, device_id),
        DashieVolumeNumber(coordinator, device_id),
    ]

    async_add_entities(entities)


class DashieBrightnessNumber(DashieEntity, NumberEntity):
    """Brightness control number entity."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:brightness-6"
    _attr_translation_key = "brightness_control"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_brightness_control"
        self._attr_name = "Brightness"

    @property
    def native_value(self) -> float | None:
        """Return the current brightness as percentage."""
        if self.coordinator.data:
            # screenBrightness is 0-255, convert to percentage
            brightness = self.coordinator.data.get("screenBrightness")
            if brightness is not None:
                return round(brightness / 255 * 100)
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the brightness level."""
        # Convert percentage (0-100) to 0-255 for the device
        brightness_value = round(value / 100 * 255)
        await self.coordinator.send_command(
            API_SET_BRIGHTNESS,
            key="screenBrightness",
            value=str(brightness_value)
        )
        await self.coordinator.async_request_refresh()


class DashieVolumeNumber(DashieEntity, NumberEntity):
    """Volume control number entity."""

    _attr_native_min_value = 0
    _attr_native_max_value = 10
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:volume-high"
    _attr_translation_key = "volume"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_volume"
        self._attr_name = "Volume"

    @property
    def native_value(self) -> float | None:
        """Return the current volume level (0-10 scale)."""
        if self.coordinator.data:
            # currentVolume is 0-100, convert to 0-10 scale
            volume = self.coordinator.data.get("currentVolume")
            if volume is not None:
                return round(volume / 10)
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the volume level."""
        # Convert 0-10 to 0-100 for the API
        api_volume = int(value) * 10
        await self.coordinator.send_command(
            API_SET_VOLUME,
            level=str(api_volume),
            stream="3"  # STREAM_MUSIC
        )
        await self.coordinator.async_request_refresh()
