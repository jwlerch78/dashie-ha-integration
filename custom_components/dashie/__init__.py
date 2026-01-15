"""Dashie Lite integration for Home Assistant."""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_PASSWORD,
    API_TEXT_TO_SPEECH,
    API_LOAD_URL,
    API_SET_BRIGHTNESS,
    API_SET_VOLUME,
)
from .coordinator import DashieCoordinator
from .media_api import register_media_api_views

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

# Service schemas
SERVICE_SEND_COMMAND = "send_command"
SERVICE_LOAD_URL = "load_url"
SERVICE_SPEAK = "speak"
SERVICE_SET_BRIGHTNESS = "set_brightness"
SERVICE_SET_VOLUME = "set_volume"
SERVICE_SHOW_MESSAGE = "show_message"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Track if media API is registered (only register once)
_media_api_registered = False


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Dashie integration."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dashie Lite from a config entry."""
    global _media_api_registered

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    password = entry.data.get(CONF_PASSWORD, "")

    coordinator = DashieCoordinator(hass, host, port, password)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (only once)
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        await _async_register_services(hass)

    # Register Media API views (only once)
    if not _media_api_registered:
        register_media_api_views(hass)
        _media_api_registered = True
        _LOGGER.info("Registered Dashie Media API views")

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    # Options changed - coordinator will pick up new media_folder on next call
    _LOGGER.debug("Options updated for %s: %s", entry.entry_id, entry.options)


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register Dashie services."""

    def _get_all_coordinators() -> list[DashieCoordinator]:
        """Get all coordinators (filter out non-coordinator items)."""
        return [
            v for v in hass.data[DOMAIN].values()
            if isinstance(v, DashieCoordinator)
        ]

    async def async_send_command(call: ServiceCall) -> None:
        """Send a command to a device."""
        command = call.data["command"]
        device_id = call.data.get("device_id")

        # If device_id specified, send to that device only
        if device_id:
            for coordinator in _get_all_coordinators():
                await coordinator.send_command(command)
        else:
            # Send to all devices
            for coordinator in _get_all_coordinators():
                await coordinator.send_command(command)

    async def async_load_url(call: ServiceCall) -> None:
        """Load a URL on a device."""
        url = call.data["url"]
        for coordinator in _get_all_coordinators():
            await coordinator.send_command(API_LOAD_URL, url=url)

    async def async_speak(call: ServiceCall) -> None:
        """Speak text on a device."""
        message = call.data["message"]
        for coordinator in _get_all_coordinators():
            await coordinator.send_command(API_TEXT_TO_SPEECH, text=message)

    async def async_set_brightness(call: ServiceCall) -> None:
        """Set brightness on a device."""
        brightness = call.data["brightness"]
        # Convert percentage to 0-255
        brightness_value = round(brightness / 100 * 255)
        for coordinator in _get_all_coordinators():
            await coordinator.send_command(
                API_SET_BRIGHTNESS,
                key="screenBrightness",
                value=str(brightness_value)
            )

    async def async_set_volume(call: ServiceCall) -> None:
        """Set volume on a device."""
        volume = call.data["volume"]
        # Convert 0-10 to 0-100 for API
        api_volume = volume * 10
        for coordinator in _get_all_coordinators():
            await coordinator.send_command(
                API_SET_VOLUME,
                level=str(api_volume),
                stream="3"
            )

    async def async_show_message(call: ServiceCall) -> None:
        """Show an overlay message on a device."""
        message = call.data["message"]
        duration = call.data.get("duration", 3000)
        for coordinator in _get_all_coordinators():
            await coordinator.send_command(
                "setOverlayMessage",
                text=message,
                duration=str(duration)
            )

    # Register all services
    hass.services.async_register(DOMAIN, SERVICE_SEND_COMMAND, async_send_command)
    hass.services.async_register(DOMAIN, SERVICE_LOAD_URL, async_load_url)
    hass.services.async_register(DOMAIN, SERVICE_SPEAK, async_speak)
    hass.services.async_register(DOMAIN, SERVICE_SET_BRIGHTNESS, async_set_brightness)
    hass.services.async_register(DOMAIN, SERVICE_SET_VOLUME, async_set_volume)
    hass.services.async_register(DOMAIN, SERVICE_SHOW_MESSAGE, async_show_message)

    _LOGGER.info("Registered Dashie services")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    # Unregister services if no more entries
    remaining_coordinators = [
        v for v in hass.data[DOMAIN].values()
        if isinstance(v, DashieCoordinator)
    ]
    if not remaining_coordinators:
        hass.services.async_remove(DOMAIN, SERVICE_SEND_COMMAND)
        hass.services.async_remove(DOMAIN, SERVICE_LOAD_URL)
        hass.services.async_remove(DOMAIN, SERVICE_SPEAK)
        hass.services.async_remove(DOMAIN, SERVICE_SET_BRIGHTNESS)
        hass.services.async_remove(DOMAIN, SERVICE_SET_VOLUME)
        hass.services.async_remove(DOMAIN, SERVICE_SHOW_MESSAGE)

    return unload_ok
