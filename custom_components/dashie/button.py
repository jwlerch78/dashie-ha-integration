"""Button entities for Dashie Lite integration."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    API_LOAD_START_URL,
    API_BRING_TO_FOREGROUND,
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
        DashieReloadButton(coordinator, device_id),
        DashieForegroundButton(coordinator, device_id),
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
