"""Button entities for Dashie Lite integration."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    API_LOAD_START_URL,
    API_BRING_TO_FOREGROUND,
    API_RESTART_APP,
    API_CLEAR_CACHE,
    API_CLEAR_WEBSTORAGE,
)
from .coordinator import DashieCoordinator
from .entity import DashieEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie Lite buttons."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]

    entities = [
        # Primary buttons (frequently used)
        DashieReloadButton(coordinator, device_id),
        DashieForegroundButton(coordinator, device_id),
        # Maintenance buttons (CONFIG category)
        DashieRestartButton(coordinator, device_id),
        DashieClearCacheButton(coordinator, device_id),
        DashieClearStorageButton(coordinator, device_id),
    ]

    async_add_entities(entities)


class DashieReloadButton(DashieEntity, ButtonEntity):
    """Reload dashboard button."""

    _attr_icon = "mdi:refresh"
    _attr_translation_key = "reload"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_reload"
        self._attr_name = "Reload Dashboard"

    async def async_press(self) -> None:
        """Reload the dashboard."""
        await self.coordinator.send_command(API_LOAD_START_URL)


class DashieForegroundButton(DashieEntity, ButtonEntity):
    """Bring to foreground button."""

    _attr_icon = "mdi:arrow-up-bold-box"
    _attr_translation_key = "foreground"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_foreground"
        self._attr_name = "Bring to Foreground"

    async def async_press(self) -> None:
        """Bring app to foreground."""
        await self.coordinator.send_command(API_BRING_TO_FOREGROUND)


# =============================================================================
# Maintenance Buttons (CONFIG category)
# =============================================================================


class DashieRestartButton(DashieEntity, ButtonEntity):
    """Restart app button."""

    _attr_icon = "mdi:restart"
    _attr_translation_key = "restart"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_restart"
        self._attr_name = "Restart App"

    async def async_press(self) -> None:
        """Restart the app."""
        await self.coordinator.send_command(API_RESTART_APP)


class DashieClearCacheButton(DashieEntity, ButtonEntity):
    """Clear WebView cache button."""

    _attr_icon = "mdi:cached"
    _attr_translation_key = "clear_cache"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_clear_cache"
        self._attr_name = "Clear Cache"

    async def async_press(self) -> None:
        """Clear the WebView cache."""
        await self.coordinator.send_command(API_CLEAR_CACHE)


class DashieClearStorageButton(DashieEntity, ButtonEntity):
    """Clear WebView local storage button."""

    _attr_icon = "mdi:database-remove"
    _attr_translation_key = "clear_storage"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_clear_storage"
        self._attr_name = "Clear Storage"

    async def async_press(self) -> None:
        """Clear the WebView local storage."""
        await self.coordinator.send_command(API_CLEAR_WEBSTORAGE)
