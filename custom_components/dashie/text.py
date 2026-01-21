"""Text entities for Dashie Lite integration."""
from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    API_SET_PIN,
    API_CLEAR_PIN,
    API_SET_STRING_SETTING,
    SETTING_HA_URL,
)
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
        # =================================================================
        # CONTROLS (Primary - no category)
        # =================================================================
        DashiePinText(coordinator, device_id),

        # =================================================================
        # CONFIGURATION (CONFIG category)
        # =================================================================
        DashieDashboardUrlText(coordinator, device_id),
    ]

    async_add_entities(entities)


# =============================================================================
# Home Assistant Section (CONFIG category)
# =============================================================================


class DashieDashboardUrlText(DashieEntity, TextEntity):
    """Dashboard URL text input entity - the Home Assistant URL to load on startup."""

    _attr_mode = TextMode.TEXT
    _attr_icon = "mdi:home-assistant"
    _attr_translation_key = "dashboard_url"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the text entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_dashboard_url"
        self._attr_name = "Dashboard URL"

    @property
    def native_value(self) -> str:
        """Return the configured dashboard URL."""
        if self.coordinator.data:
            # deviceInfo returns "startUrl", not "dashboardUrl"
            return self.coordinator.data.get("startUrl", "")
        return ""

    async def async_set_value(self, value: str) -> None:
        """Set the dashboard URL."""
        if value:
            await self.coordinator.send_command(
                API_SET_STRING_SETTING, key=SETTING_HA_URL, value=value
            )
            await self.coordinator.async_request_refresh()


class DashiePinText(DashieEntity, TextEntity):
    """PIN code text input entity for lock - PRIMARY CONTROL."""

    _attr_mode = TextMode.PASSWORD
    _attr_icon = "mdi:form-textbox-password"
    _attr_translation_key = "pin"
    # No EntityCategory = Primary control (shown in Controls)
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
            # Optimistically update local state for immediate UI feedback
            self.coordinator.update_local_data(hasPinSet=False)
        elif value and len(value) == 4 and value.isdigit():
            # Set the PIN to a new 4-digit value
            _LOGGER.info("Setting new PIN")
            self.coordinator.set_stored_pin(value)  # Store PIN for unlocking
            await self.coordinator.send_command(API_SET_PIN, pin=value)
            # Optimistically update local state for immediate UI feedback
            self.coordinator.update_local_data(hasPinSet=True)
        else:
            # Clear the PIN (empty or invalid = clear)
            _LOGGER.info("Clearing PIN (value: '%s')", value)
            self.coordinator.set_stored_pin("")  # Clear stored PIN
            await self.coordinator.send_command(API_CLEAR_PIN)
            # Optimistically update local state for immediate UI feedback
            self.coordinator.update_local_data(hasPinSet=False)
        await self.coordinator.async_request_refresh()
