"""Update platform for Dashie integration."""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import aiohttp

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# GitHub API endpoint for releases
GITHUB_API_RELEASES = "https://api.github.com/repos/jwlerch78/dashie-ha-integration/releases/latest"

# Check for updates every 6 hours
UPDATE_CHECK_INTERVAL = timedelta(hours=6)


def _get_current_version() -> str:
    """Read current version from manifest.json."""
    try:
        manifest_path = Path(__file__).parent / "manifest.json"
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
            return manifest.get("version", "unknown")
    except Exception as err:
        _LOGGER.warning("Could not read version from manifest: %s", err)
        return "unknown"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie update entity."""
    # Only create one update entity for the entire integration, not per device
    # Check if we've already created the update entity
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    if hass.data[DOMAIN].get("_update_entity_created"):
        # Update entity already exists, skip creating another
        return

    # Mark that we're creating the update entity
    hass.data[DOMAIN]["_update_entity_created"] = True

    # Create a coordinator for checking GitHub releases
    update_coordinator = DashieUpdateCoordinator(hass)

    # Do initial fetch
    await update_coordinator.async_config_entry_first_refresh()

    async_add_entities([DashieUpdateEntity(update_coordinator, entry)])


class DashieUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to check for Dashie integration updates."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the update coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Dashie Integration Update",
            update_interval=UPDATE_CHECK_INTERVAL,
        )
        self._session = async_get_clientsession(hass)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest release info from GitHub."""
        try:
            async with self._session.get(
                GITHUB_API_RELEASES,
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        "latest_version": data.get("tag_name", "").lstrip("v"),
                        "release_url": data.get("html_url"),
                        "release_notes": data.get("body", ""),
                        "published_at": data.get("published_at"),
                    }
                elif response.status == 403:
                    # Rate limited
                    _LOGGER.debug("GitHub API rate limited, will retry later")
                    return self.data or {}
                else:
                    _LOGGER.warning(
                        "Failed to fetch GitHub release: HTTP %s", response.status
                    )
                    return self.data or {}
        except aiohttp.ClientError as err:
            _LOGGER.debug("Error fetching GitHub release: %s", err)
            return self.data or {}
        except Exception as err:
            _LOGGER.warning("Unexpected error fetching GitHub release: %s", err)
            return self.data or {}


class DashieUpdateEntity(CoordinatorEntity[DashieUpdateCoordinator], UpdateEntity):
    """Update entity for Dashie integration.

    This entity is created only once per integration (not per device).
    It does not have a device_info so it appears as a standalone entity
    without creating a separate "Dashie Integration" device.
    """

    _attr_has_entity_name = False  # Use name directly, not device + entity name
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.RELEASE_NOTES
    _attr_title = "Dashie Integration"

    def __init__(
        self,
        coordinator: DashieUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the update entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{DOMAIN}_integration_update"
        self._attr_name = "Dashie Integration Update"
        self._current_version = _get_current_version()

    # Note: No device_info property - this entity is standalone and doesn't
    # create a separate device. This prevents the "Dashie Integration" device
    # from appearing under each Dashie device in the UI.

    @property
    def installed_version(self) -> str | None:
        """Return the current installed version."""
        return self._current_version

    @property
    def latest_version(self) -> str | None:
        """Return the latest available version."""
        if self.coordinator.data:
            return self.coordinator.data.get("latest_version")
        return None

    @property
    def release_url(self) -> str | None:
        """Return the URL to the release notes."""
        if self.coordinator.data:
            return self.coordinator.data.get("release_url")
        return "https://github.com/jwlerch78/dashie-ha-integration/releases"

    async def async_release_notes(self) -> str | None:
        """Return the release notes for the latest version."""
        if self.coordinator.data:
            notes = self.coordinator.data.get("release_notes", "")
            if notes:
                return notes
        return "See GitHub for release notes."
