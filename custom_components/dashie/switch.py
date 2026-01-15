"""Switch entities for Dashie Lite integration."""
from __future__ import annotations

import logging
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
    API_START_SCREENSAVER,
    API_STOP_SCREENSAVER,
    API_SET_DARK_MODE,
)
from .coordinator import DashieCoordinator
from .entity import DashieEntity

_LOGGER = logging.getLogger(__name__)


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
        DashieScreensaverSwitch(coordinator, device_id),
        DashieLockSwitch(coordinator, device_id),
    ]

    # Dark mode switch (may be unavailable on Fire Tablets)
    entities.append(DashieDarkModeSwitch(coordinator, device_id))

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
        """Return true if screen is on (not in black/off state)."""
        if self.coordinator.data:
            # isScreenOn is False only when in true "screen off" black mode
            return self.coordinator.data.get("isScreenOn", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the screen on (wake from black/off state)."""
        await self.coordinator.send_command(API_SCREEN_ON)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the screen off."""
        await self.coordinator.send_command(API_SCREEN_OFF)
        await self.coordinator.async_request_refresh()


class DashieScreensaverSwitch(DashieEntity, SwitchEntity):
    """Screensaver switch."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:sleep"
    _attr_translation_key = "screensaver"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_screensaver"
        self._attr_name = "Screensaver"

    @property
    def is_on(self) -> bool | None:
        """Return true if screensaver is active."""
        if self.coordinator.data:
            return self.coordinator.data.get("isInScreensaver", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the screensaver."""
        await self.coordinator.send_command(API_START_SCREENSAVER)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the screensaver."""
        await self.coordinator.send_command(API_STOP_SCREENSAVER)
        await self.coordinator.async_request_refresh()


class DashieLockSwitch(DashieEntity, SwitchEntity):
    """Lock switch (PIN required if set)."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:lock"
    _attr_translation_key = "lock"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_lock"
        self._attr_name = "Lock"

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
        """Unlock the kiosk (API password is authentication, PIN not required)."""
        await self.coordinator.send_command(API_UNLOCK_KIOSK)
        await self.coordinator.async_request_refresh()


class DashieDarkModeSwitch(DashieEntity, SwitchEntity):
    """Dark mode switch."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:theme-light-dark"
    _attr_translation_key = "dark_mode"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_dark_mode"
        self._attr_name = "Dark Mode"

    @property
    def available(self) -> bool:
        """Return True if dark mode is supported on this device."""
        # First check parent availability (coordinator connected)
        if not super().available:
            _LOGGER.info("Dark mode: parent not available")
            return False
        if not self.coordinator.data:
            _LOGGER.info("Dark mode: no coordinator data")
            return False
        # Fire Tablets don't support dark mode (requires WRITE_SECURE_SETTINGS)
        supported = self.coordinator.data.get("supportsDarkMode", True)
        _LOGGER.info("Dark mode available check: supportsDarkMode=%s, data keys=%s",
                     supported, list(self.coordinator.data.keys())[:10])
        return supported

    @property
    def is_on(self) -> bool | None:
        """Return true if dark mode is enabled."""
        if self.coordinator.data:
            return self.coordinator.data.get("isDarkMode", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable dark mode."""
        await self.coordinator.send_command(API_SET_DARK_MODE, value="true")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable dark mode."""
        await self.coordinator.send_command(API_SET_DARK_MODE, value="false")
        await self.coordinator.async_request_refresh()
