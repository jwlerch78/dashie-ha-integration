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
from .photo_hub import PhotoHub
from .photo_api import register_photo_api_views
from .panel import async_register_panel

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

# Allow loading without config entry (for Photo Hub standalone)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Dashie integration (runs even without config entries)."""
    hass.data.setdefault(DOMAIN, {})

    # Initialize Photo Hub on integration load (works without devices)
    if "photo_hub" not in hass.data[DOMAIN]:
        photo_hub = PhotoHub(hass)
        if await photo_hub.async_initialize():
            hass.data[DOMAIN]["photo_hub"] = photo_hub
            register_photo_api_views(hass)
            await async_register_panel(hass)
            _LOGGER.info("Photo Hub initialized (standalone mode)")
        else:
            _LOGGER.warning("Failed to initialize Photo Hub")

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dashie Lite from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    password = entry.data.get(CONF_PASSWORD, "")

    coordinator = DashieCoordinator(hass, host, port, password)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (only once, check if already registered)
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        await _async_register_services(hass)

    # Initialize Photo Hub (only once)
    if "photo_hub" not in hass.data[DOMAIN]:
        photo_hub = PhotoHub(hass)
        if await photo_hub.async_initialize():
            hass.data[DOMAIN]["photo_hub"] = photo_hub
            register_photo_api_views(hass)
            await async_register_panel(hass)
            _LOGGER.info("Photo Hub initialized and API views registered")
        else:
            _LOGGER.warning("Failed to initialize Photo Hub")

    return True


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register Dashie services."""

    def _get_coordinator(entry_id: str) -> DashieCoordinator | None:
        """Get coordinator by entry ID."""
        return hass.data[DOMAIN].get(entry_id)

    def _get_all_coordinators() -> list[DashieCoordinator]:
        """Get all coordinators."""
        return list(hass.data[DOMAIN].values())

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
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_SEND_COMMAND)
        hass.services.async_remove(DOMAIN, SERVICE_LOAD_URL)
        hass.services.async_remove(DOMAIN, SERVICE_SPEAK)
        hass.services.async_remove(DOMAIN, SERVICE_SET_BRIGHTNESS)
        hass.services.async_remove(DOMAIN, SERVICE_SET_VOLUME)
        hass.services.async_remove(DOMAIN, SERVICE_SHOW_MESSAGE)

    return unload_ok
