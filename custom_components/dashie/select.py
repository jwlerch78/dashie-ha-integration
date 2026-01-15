"""Select entities for Dashie Lite integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    API_SET_SCREENSAVER_MODE,
    API_SET_HA_MEDIA_FOLDER,
)
from .coordinator import DashieCoordinator
from .entity import DashieEntity
from .media_api import _get_media_base_path, _list_media_folders

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie Lite select entities."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]

    entities = [
        DashieScreensaverModeSelect(coordinator, device_id),
        DashieScreensaverPhotoFolderSelect(coordinator, device_id, hass),
    ]

    async_add_entities(entities)


class DashieScreensaverModeSelect(DashieEntity, SelectEntity):
    """Screensaver mode select."""

    _attr_icon = "mdi:sleep"
    _attr_translation_key = "screensaver_mode"

    # Map API values to display values
    _mode_display_map = {
        "dim": "Dim",
        "black": "Black",
        "url": "URL",
        "photos": "Photos",
        "app": "App",
    }

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the select."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_screensaver_mode"
        self._attr_name = "Screensaver Mode"
        self._attr_options = ["Dim", "Black", "URL", "Photos", "App"]

    @property
    def current_option(self) -> str | None:
        """Return the current screensaver mode."""
        if self.coordinator.data:
            mode = self.coordinator.data.get("screensaverMode", "dim")
            # Use display map for proper capitalization (especially for URL)
            return self._mode_display_map.get(mode, mode.capitalize() if mode else "Dim")
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the screensaver mode."""
        # Convert display value to API value (lowercase)
        mode = option.lower()
        await self.coordinator.send_command(API_SET_SCREENSAVER_MODE, mode=mode)
        await self.coordinator.async_request_refresh()


class DashieScreensaverPhotoFolderSelect(DashieEntity, SelectEntity):
    """Screensaver photo folder select - dynamically fetches folders from HA Media."""

    _attr_icon = "mdi:folder-image"
    _attr_translation_key = "screensaver_photo_src_folder"

    def __init__(self, coordinator: DashieCoordinator, device_id: str, hass: HomeAssistant) -> None:
        """Initialize the select."""
        super().__init__(coordinator, device_id)
        self._hass = hass
        self._attr_unique_id = f"{device_id}_screensaver_photo_src_folder"
        self._attr_name = "Screensaver Photo Src Folder"
        self._cached_folders: list[dict] = []
        self._attr_options = ["(root)"]  # Default until we fetch folders

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        await super().async_added_to_hass()
        # Fetch available folders
        await self._update_folder_options()

    async def _update_folder_options(self) -> None:
        """Fetch available folders from HA Media and update options."""
        try:
            media_dir = _get_media_base_path(self._hass)
            if media_dir.exists():
                folders = await self._hass.async_add_executor_job(_list_media_folders, media_dir)
                self._cached_folders = folders
                # Build options list - use folder name for display, path for value
                options = []
                for folder in folders:
                    if folder["path"] == "*":
                        options.append("All")
                    elif folder["path"] == ".":
                        options.append("(root)")
                    else:
                        options.append(folder["name"])
                if options:
                    self._attr_options = options
                else:
                    self._attr_options = ["(no folders)"]
                _LOGGER.debug("Updated folder options: %s", self._attr_options)
        except Exception as e:
            _LOGGER.warning("Failed to fetch media folders: %s", e)
            self._attr_options = ["(error)"]

    @property
    def current_option(self) -> str | None:
        """Return the current folder."""
        if self.coordinator.data:
            folder_path = self.coordinator.data.get("haMediaFolder", ".")
            # Map path to display name
            if folder_path == "*":
                return "All"
            if folder_path == ".":
                return "(root)"
            # Find the folder name that matches this path
            for folder in self._cached_folders:
                if folder["path"] == folder_path:
                    return folder["name"]
            # If not found in cache, return as-is
            return folder_path
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the photo folder."""
        # Map display name back to path
        if option == "All":
            folder_path = "*"
        elif option == "(root)":
            folder_path = "."
        else:
            # Find the path for this folder name
            folder_path = option  # Default to using the option as path
            for folder in self._cached_folders:
                if folder["name"] == option:
                    folder_path = folder["path"]
                    break

        await self.coordinator.send_command(API_SET_HA_MEDIA_FOLDER, folder=folder_path)
        await self.coordinator.async_request_refresh()

    async def async_update(self) -> None:
        """Update entity state - also refresh folder list periodically."""
        await super().async_update()
        # Refresh folder list if we don't have any cached
        if not self._cached_folders or len(self._cached_folders) == 0:
            await self._update_folder_options()
