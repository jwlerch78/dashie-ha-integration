"""Switch entities for Dashie Lite integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    API_SCREEN_ON,
    API_SCREEN_OFF,
    API_LOCK_KIOSK,
    API_UNLOCK_KIOSK,
)
from .coordinator import DashieCoordinator
from .entity import DashieEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie Lite switches."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]

    entities = [
        DashieScreenSwitch(coordinator, device_id),
        DashieKioskLockSwitch(coordinator, device_id),
    ]

    async_add_entities(entities)


class DashieScreenSwitch(DashieEntity, SwitchEntity):
    """Screen on/off switch."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:monitor"
    _attr_translation_key = "screen"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_screen"
        self._attr_name = "Screen"

    @property
    def is_on(self) -> bool | None:
        """Return true if screen is on."""
        if self.coordinator.data:
            return self.coordinator.data.get("isScreenOn", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the screen on."""
        await self.coordinator.send_command(API_SCREEN_ON)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the screen off."""
        await self.coordinator.send_command(API_SCREEN_OFF)
        await self.coordinator.async_request_refresh()


class DashieKioskLockSwitch(DashieEntity, SwitchEntity):
    """Kiosk lock switch."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:lock"
    _attr_translation_key = "kiosk_lock"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_kiosk_lock"
        self._attr_name = "Kiosk Lock"

    @property
    def is_on(self) -> bool | None:
        """Return true if kiosk is locked."""
        if self.coordinator.data:
            return self.coordinator.data.get("kioskLocked", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock the kiosk."""
        await self.coordinator.send_command(API_LOCK_KIOSK)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unlock the kiosk."""
        await self.coordinator.send_command(API_UNLOCK_KIOSK)
        await self.coordinator.async_request_refresh()
