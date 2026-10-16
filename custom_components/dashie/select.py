"""Select entities for Dashie integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    API_SET_SCREENSAVER_MODE,
    API_SET_SCREEN_OFF_METHOD,
    API_SET_HA_MEDIA_FOLDER,
    API_SET_STRING_SETTING,
    SETTING_MOTION_WAKE_MODE,
    MOTION_WAKE_MODES,
    SCREEN_OFF_METHODS,
)
from .coordinator import DashieCoordinator
from .entity import DashieEntity
import asyncio
import time

from .media_api import _get_media_base_path, _list_media_folder_names

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie select entities."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]

    entities = [
        DashieScreensaverModeSelect(coordinator, device_id),
        DashieScreensaverPhotoFolderSelect(coordinator, device_id, hass),
        DashieMotionWakeModeSelect(coordinator, device_id),
        DashieScreenOffMethodSelect(coordinator, device_id),
    ]

    async_add_entities(entities)


class DashieScreensaverModeSelect(DashieEntity, SelectEntity):
    """Screensaver mode select."""

    _attr_icon = "mdi:sleep"
    _attr_translation_key = "screensaver_mode"
    _attr_entity_category = EntityCategory.CONFIG

    # Map API values to display values
    _mode_display_map = {
        "dim": "Dim",
        "black": "Black Overlay",
        "off": "Screen Off",
        "url": "URL",
        "photos": "Photos",
        "weather": "Weather & Time",
        "ha_page": "HA Page",
        "app": "App",
    }

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the select."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_screensaver_mode"
        self._attr_name = "Screensaver: Mode"
        self._attr_options = [
            "Dim", "Black Overlay", "Screen Off",
            "Photos", "Weather & Time", "HA Page", "URL", "App",
        ]

    @property
    def current_option(self) -> str | None:
        """Return the current screensaver mode."""
        if self.coordinator.data:
            mode = self.coordinator.data.get("screensaverMode", "dim")
            # Use display map for proper capitalization (especially for URL)
            return self._mode_display_map.get(mode, mode.capitalize() if mode else "Dim")
        return None

    # Reverse map: display value -> API value
    _mode_api_map = {v: k for k, v in _mode_display_map.items()}

    async def async_select_option(self, option: str) -> None:
        """Set the screensaver mode."""
        mode = self._mode_api_map.get(option, option.lower())
        await self.coordinator.async_command(API_SET_SCREENSAVER_MODE, mode=mode)
        # Optimistic update so the UI reflects the new value immediately
        # instead of waiting for the next coordinator poll. A refused or
        # unanswered command raised above, so this only runs on success.
        if self.coordinator.data is not None:
            self.coordinator.data["screensaverMode"] = mode
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class DashieScreensaverPhotoFolderSelect(DashieEntity, SelectEntity):
    """Screensaver photo folder select - dynamically fetches folders from HA Media."""

    _attr_icon = "mdi:folder-image"
    _attr_translation_key = "screensaver_photo_src_folder"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str, hass: HomeAssistant) -> None:
        """Initialize the select."""
        super().__init__(coordinator, device_id)
        self._hass = hass
        self._attr_unique_id = f"{device_id}_screensaver_photo_src_folder"
        self._attr_name = "Screensaver: Photo Src Folder"
        self._cached_folders: list[dict] = []
        self._attr_options = ["(root)"]  # Default until we fetch folders
        # ``_cached_folders == []`` must NOT be the "not scanned yet" sentinel. It
        # conflated "never scanned" with "scanned and found nothing", and
        # async_update retried on that emptiness every SCAN_INTERVAL (5 s) — so a
        # /media with subfolders but no images (Frigate recordings: SUPPORTED_EXTENSIONS
        # has no .mp4) re-walked the whole tree forever, successfully, with no
        # exception and nothing above debug in the log. D-279.
        self._scanned = False
        self._scan_failures = 0
        self._next_scan_allowed = 0.0
        self._scan_task = None

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass.

        The folder scan is NOT awaited here. Awaiting it put a filesystem walk on
        HA's setup critical path, and on a large /media it outran the bootstrap
        stage-2 timeout, so the whole config entry landed in ``setup_error`` on
        every restart (D-279). It runs in the background instead; options stay at
        their default until it lands.

        The task is cancelled when the entity is removed. Without that, the job
        outlives an unload and its completion races a re-setup.
        """
        await super().async_added_to_hass()
        self._scan_task = self._hass.async_create_background_task(
            self._update_folder_options(),
            name=f"dashie media folder scan {self._attr_unique_id}",
        )
        self.async_on_remove(self._cancel_scan)

    def _cancel_scan(self) -> None:
        """Stop an in-flight scan so it cannot outlive this entity."""
        task = self._scan_task
        if task is not None and not task.done():
            task.cancel()

    async def _update_folder_options(self) -> None:
        """Fetch available folders from HA Media and update options."""
        try:
            media_dir = await self._hass.async_add_executor_job(_get_media_base_path, self._hass)
            if media_dir.exists():
                folders = await self._hass.async_add_executor_job(
                    _list_media_folder_names, media_dir
                )
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
            # Scanned, whatever the result. An empty result is an ANSWER, not a
            # reason to scan again.
            self._scanned = True
            self._scan_failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Also "scanned": a failure must not leave the empty-list sentinel
            # behind for async_update to retry twice a minute forever.
            self._scanned = True
            self._scan_failures += 1
            delay = min(60 * 2 ** (self._scan_failures - 1), 3600)
            self._next_scan_allowed = time.monotonic() + delay
            _LOGGER.warning(
                "DROP: media folder scan failed (%s attempt(s)), retrying in %ss: %s",
                self._scan_failures, delay, e,
            )
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

        await self.coordinator.async_command(API_SET_HA_MEDIA_FOLDER, folder=folder_path)
        await self.coordinator.async_request_refresh()

    async def async_update(self) -> None:
        """Update entity state - also refresh folder list periodically."""
        await super().async_update()
        # Re-scan only if we have never completed one, or if the last one FAILED
        # and its backoff has expired. Never because the result was empty.
        if self._scanned and (
            self._scan_failures == 0 or time.monotonic() < self._next_scan_allowed
        ):
            return
        await self._update_folder_options()


class DashieMotionWakeModeSelect(DashieEntity, SelectEntity):
    """Motion wake mode select - how the device wakes from screensaver."""

    _attr_icon = "mdi:motion-sensor"
    _attr_translation_key = "motion_wake_mode"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the select."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_motion_wake_mode"
        self._attr_name = "Screensaver: Motion Wake"
        # Options from MOTION_WAKE_MODES values
        self._attr_options = list(MOTION_WAKE_MODES.values())

    @property
    def current_option(self) -> str | None:
        """Return the current motion wake mode."""
        if self.coordinator.data:
            mode = self.coordinator.data.get("motionWakeMode", "disabled")
            return MOTION_WAKE_MODES.get(mode, "Disabled")
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the motion wake mode."""
        # Find the API value for this display option
        mode_key = "disabled"
        for key, value in MOTION_WAKE_MODES.items():
            if value == option:
                mode_key = key
                break
        await self.coordinator.async_command(
            API_SET_STRING_SETTING, key=SETTING_MOTION_WAKE_MODE, value=mode_key
        )
        await self.coordinator.async_request_refresh()


class DashieScreenOffMethodSelect(DashieEntity, SelectEntity):
    """Screen off method select - overlay (fast wake) vs hardware (real off)."""

    _attr_icon = "mdi:monitor-off"
    _attr_translation_key = "screen_off_method"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the select."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_screen_off_method"
        self._attr_name = "Screen Off Behavior"
        self._attr_options = list(SCREEN_OFF_METHODS.values())

    @property
    def current_option(self) -> str | None:
        """Return the current screen off method."""
        if self.coordinator.data:
            method = self.coordinator.data.get("screenOffMethod", "overlay")
            return SCREEN_OFF_METHODS.get(method, "Black Overlay")
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the screen off method."""
        # Find the API value for this display option
        method_key = "overlay"
        for key, value in SCREEN_OFF_METHODS.items():
            if value == option:
                method_key = key
                break
        await self.coordinator.async_command(
            API_SET_SCREEN_OFF_METHOD, method=method_key
        )
        await self.coordinator.async_request_refresh()
