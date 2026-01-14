"""Sensor entities for Dashie Lite integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation, LIGHT_LUX
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
        DashieLightSensor(coordinator, device_id),
        DashieCurrentPageSensor(coordinator, device_id),
        DashieWifiSignalSensor(coordinator, device_id),
        DashieStorageSensor(coordinator, device_id),
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


class DashieLightSensor(DashieEntity, SensorEntity):
    """Ambient light sensor."""

    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_native_unit_of_measurement = LIGHT_LUX
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:brightness-5"
    _attr_translation_key = "light"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_light"
        self._attr_name = "Ambient Light"

    @property
    def native_value(self) -> int | None:
        """Return the ambient light level in lux."""
        if self.coordinator.data:
            return self.coordinator.data.get("ambientLight")
        return None


class DashieCurrentPageSensor(DashieEntity, SensorEntity):
    """Current page/URL sensor."""

    _attr_icon = "mdi:web"
    _attr_translation_key = "current_page"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_current_page"
        self._attr_name = "Current Page"

    @property
    def native_value(self) -> str | None:
        """Return the current page URL."""
        if self.coordinator.data:
            return self.coordinator.data.get("currentPage")
        return None


class DashieWifiSignalSensor(DashieEntity, SensorEntity):
    """WiFi signal strength sensor."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi"
    _attr_translation_key = "wifi_signal"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_wifi_signal"
        self._attr_name = "WiFi Signal"

    @property
    def native_value(self) -> int | None:
        """Return the WiFi signal strength as percentage."""
        if self.coordinator.data:
            return self.coordinator.data.get("wifiSignalLevel")
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional WiFi attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "ssid": self.coordinator.data.get("ssid"),
            "ip_address": self.coordinator.data.get("ip4"),
            "mac_address": self.coordinator.data.get("Mac"),
        }


class DashieStorageSensor(DashieEntity, SensorEntity):
    """Internal storage sensor."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:harddisk"
    _attr_translation_key = "storage"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_storage"
        self._attr_name = "Storage Free"

    @property
    def native_value(self) -> float | None:
        """Return the free storage in GB."""
        if self.coordinator.data:
            free_bytes = self.coordinator.data.get("internalStorageFreeSpace")
            if free_bytes is not None:
                return round(free_bytes / (1024 ** 3), 2)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional storage attributes."""
        if not self.coordinator.data:
            return {}
        total_bytes = self.coordinator.data.get("internalStorageTotalSpace")
        return {
            "total_gb": round(total_bytes / (1024 ** 3), 2) if total_bytes else None,
        }
