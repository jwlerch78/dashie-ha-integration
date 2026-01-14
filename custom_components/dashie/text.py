"""Text entities for Dashie Lite integration."""
from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_DEVICE_ID, API_LOAD_URL, API_SET_PIN, API_CLEAR_PIN
from .coordinator import DashieCoordinator
from .entity import DashieEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie Lite text entities."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]

    entities = [
        DashieLoadUrlText(coordinator, device_id),
        DashiePinText(coordinator, device_id),
    ]

    async_add_entities(entities)


class DashieLoadUrlText(DashieEntity, TextEntity):
    """Load URL text input entity."""

    _attr_mode = TextMode.TEXT
    _attr_icon = "mdi:link"
    _attr_translation_key = "load_url"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the text entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_load_url"
        self._attr_name = "Load URL"
        self._current_value = ""

    @property
    def native_value(self) -> str:
        """Return the current URL (from current page sensor)."""
        if self.coordinator.data:
            return self.coordinator.data.get("currentPage", "")
        return ""

    async def async_set_value(self, value: str) -> None:
        """Load the specified URL."""
        if value:
            await self.coordinator.send_command(API_LOAD_URL, url=value)
            await self.coordinator.async_request_refresh()


class DashiePinText(DashieEntity, TextEntity):
    """PIN code text input entity for lock."""

    _attr_mode = TextMode.PASSWORD
    _attr_icon = "mdi:form-textbox-password"
    _attr_translation_key = "pin"
    _attr_native_min = 0
    _attr_native_max = 4
    # Pattern allows digits OR **** for display when PIN is set
    _attr_pattern = r"^(\d{0,4}|\*{4})$"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the text entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_pin"
        self._attr_name = "Lock PIN"

    @property
    def native_value(self) -> str:
        """Return masked PIN status (not actual PIN for security)."""
        if self.coordinator.data:
            has_pin = self.coordinator.data.get("hasPinSet", False)
            return "****" if has_pin else ""
        return ""

    async def async_set_value(self, value: str) -> None:
        """Set or clear the PIN."""
        _LOGGER.debug("PIN set_value called with: '%s' (len=%d)", value, len(value) if value else 0)

        # Treat "****" (the masked display value) as "clear PIN" request
        if value == "****":
            _LOGGER.info("Clearing PIN (masked value submitted)")
            self.coordinator.set_stored_pin("")  # Clear stored PIN
            await self.coordinator.send_command(API_CLEAR_PIN)
        elif value and len(value) == 4 and value.isdigit():
            # Set the PIN to a new 4-digit value
            _LOGGER.info("Setting new PIN")
            self.coordinator.set_stored_pin(value)  # Store PIN for unlocking
            await self.coordinator.send_command(API_SET_PIN, pin=value)
        else:
            # Clear the PIN (empty or invalid = clear)
            _LOGGER.info("Clearing PIN (value: '%s')", value)
            self.coordinator.set_stored_pin("")  # Clear stored PIN
            await self.coordinator.send_command(API_CLEAR_PIN)
        await self.coordinator.async_request_refresh()
