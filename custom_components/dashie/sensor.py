"""Sensor entities for Dashie Lite integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_DEVICE_ID
from .coordinator import DashieCoordinator
from .entity import DashieEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie Lite sensors."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]

    entities = [
        DashieBatterySensor(coordinator, device_id),
        DashieBrightnessSensor(coordinator, device_id),
        DashieMemorySensor(coordinator, device_id),
    ]

    async_add_entities(entities)


class DashieBatterySensor(DashieEntity, SensorEntity):
    """Battery level sensor."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "battery"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_battery"
        self._attr_name = "Battery"

    @property
    def native_value(self) -> int | None:
        """Return the battery level."""
        if self.coordinator.data:
            return self.coordinator.data.get("batteryLevel")
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional battery attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "plugged": self.coordinator.data.get("plugged"),
        }


class DashieBrightnessSensor(DashieEntity, SensorEntity):
    """Screen brightness sensor."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:brightness-6"
    _attr_translation_key = "brightness"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_brightness"
        self._attr_name = "Brightness"

    @property
    def native_value(self) -> int | None:
        """Return the brightness level as percentage."""
        if self.coordinator.data:
            # screenBrightness is 0-255, convert to percentage
            brightness = self.coordinator.data.get("screenBrightness")
            if brightness is not None:
                return round(brightness / 255 * 100)
        return None


class DashieMemorySensor(DashieEntity, SensorEntity):
    """Memory usage sensor."""

    _attr_native_unit_of_measurement = UnitOfInformation.MEGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:memory"
    _attr_translation_key = "memory"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_memory"
        self._attr_name = "Memory Used"

    @property
    def native_value(self) -> float | None:
        """Return memory usage in MB."""
        if self.coordinator.data:
            # Try heap_used_mb first (from telemetry), fallback to calculation
            heap_used = self.coordinator.data.get("heap_used_mb")
            if heap_used is not None:
                return round(heap_used, 1)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional memory attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "total_ram_mb": self.coordinator.data.get("total_ram_mb"),
            "available_ram_mb": self.coordinator.data.get("available_ram_mb"),
            "low_memory": self.coordinator.data.get("low_memory"),
        }
