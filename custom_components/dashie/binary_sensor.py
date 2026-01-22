"""Binary sensor entities for Dashie Lite integration."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_DEVICE_ID
from .coordinator import DashieCoordinator
from .entity import DashieEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie Lite binary sensors."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]

    entities = [
        DashiePluggedSensor(coordinator, device_id),
        DashieScreensaverSensor(coordinator, device_id),
        DashiePinSetSensor(coordinator, device_id),
        DashieDeviceAdminSensor(coordinator, device_id),
    ]

    async_add_entities(entities)


class DashiePluggedSensor(DashieEntity, BinarySensorEntity):
    """Device plugged in binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_translation_key = "plugged"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_plugged"
        self._attr_name = "Plugged In"

    @property
    def is_on(self) -> bool | None:
        """Return true if device is plugged in."""
        if self.coordinator.data:
            return self.coordinator.data.get("plugged", False)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "plug_source": self.coordinator.data.get("plugSource"),
        }


class DashieScreensaverSensor(DashieEntity, BinarySensorEntity):
    """Screensaver active binary sensor."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:sleep"
    _attr_translation_key = "screensaver_active"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_screensaver_active"
        self._attr_name = "Screensaver Active"

    @property
    def is_on(self) -> bool | None:
        """Return true if screensaver is active."""
        if self.coordinator.data:
            return self.coordinator.data.get("isInScreensaver", False)
        return None


class DashiePinSetSensor(DashieEntity, BinarySensorEntity):
    """PIN set binary sensor."""

    # Don't use LOCK device class - it shows "Unlocked" when is_on=True which is confusing
    # We want: PIN set (True) = "Set", PIN not set (False) = "Not Set"
    _attr_translation_key = "pin_set"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_pin_set"
        self._attr_name = "PIN Set"

    @property
    def is_on(self) -> bool | None:
        """Return true if PIN is set."""
        if self.coordinator.data:
            return self.coordinator.data.get("hasPinSet", False)
        return None

    @property
    def icon(self) -> str:
        """Return icon based on PIN state."""
        if self.is_on:
            return "mdi:lock-check"
        return "mdi:lock-open-outline"


class DashieDeviceAdminSensor(DashieEntity, BinarySensorEntity):
    """Device Admin enabled binary sensor.

    Indicates whether Dashie Lite has Device Admin permission, which is required
    for hardware screen off. Without this permission, screenOff falls back to
    a black overlay instead of actually turning off the display hardware.
    """

    _attr_translation_key = "device_admin"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_device_admin"
        self._attr_name = "Device Admin"

    @property
    def is_on(self) -> bool | None:
        """Return true if Device Admin is enabled."""
        if self.coordinator.data:
            return self.coordinator.data.get("isDeviceAdmin", False)
        return None

    @property
    def icon(self) -> str:
        """Return icon based on Device Admin state."""
        if self.is_on:
            return "mdi:shield-check"
        return "mdi:shield-alert-outline"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes explaining the permission."""
        return {
            "description": "Device Admin permission is required for hardware screen off. "
                           "Without it, screenOff uses a black overlay instead.",
            "how_to_enable": "In Dashie Lite settings, set screensaver mode to 'Screen Off' "
                             "and grant the permission when prompted.",
        }
