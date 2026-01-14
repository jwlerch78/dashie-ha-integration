"""Text entities for Dashie Lite integration."""
from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_DEVICE_ID, API_LOAD_URL
from .coordinator import DashieCoordinator
from .entity import DashieEntity


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
