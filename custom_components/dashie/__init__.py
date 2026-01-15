"""Dashie Lite integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import timedelta
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.event import async_track_state_change_event, async_call_later
from homeassistant.const import STATE_IDLE, STATE_PAUSED

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

# Timer services
SERVICE_START_TIMER = "start_timer"
SERVICE_PAUSE_TIMER = "pause_timer"
SERVICE_CANCEL_TIMER = "cancel_timer"
SERVICE_SHOW_TIMER = "show_timer"

# Timer state tracking
TIMER_STATE_ACTIVE = "active"
TIMER_STATE_PAUSED = "paused"
TIMER_STATE_IDLE = "idle"

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

    # --- Timer Services ---

    def _format_duration(seconds: int) -> str:
        """Format seconds into human-readable duration."""
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        secs = seconds % 60
        if minutes < 60:
            return f"{minutes}:{secs:02d}"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}:{mins:02d}:{secs:02d}"

    def _parse_duration(duration_str: str) -> int:
        """Parse duration string (HH:MM:SS or MM:SS or seconds) to seconds."""
        if isinstance(duration_str, (int, float)):
            return int(duration_str)

        parts = str(duration_str).split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        else:
            return int(duration_str)

    async def _show_timer_on_devices(
        timer_entity_id: str,
        label: str,
        remaining: str,
        state: str,
        duration_ms: int = 0
    ) -> None:
        """Send timer overlay to all Dashie devices."""
        for coordinator in _get_all_coordinators():
            await coordinator.send_command(
                "showTimer",
                timerId=timer_entity_id,
                label=label,
                remaining=remaining,
                state=state,
                duration=str(duration_ms) if duration_ms else "0"
            )

    async def _hide_timer_on_devices(timer_entity_id: str) -> None:
        """Hide timer overlay on all Dashie devices."""
        for coordinator in _get_all_coordinators():
            await coordinator.send_command(
                "hideTimer",
                timerId=timer_entity_id
            )

    async def async_start_timer(call: ServiceCall) -> None:
        """Start a timer and show it on Dashie devices."""
        timer_entity = call.data.get("timer_entity")
        duration = call.data.get("duration")
        label = call.data.get("label", "")

        if not timer_entity:
            _LOGGER.error("timer_entity is required for start_timer")
            return

        # Validate timer entity exists
        state = hass.states.get(timer_entity)
        if state is None:
            _LOGGER.error("Timer entity %s not found", timer_entity)
            return

        # Parse duration if provided
        service_data = {"entity_id": timer_entity}
        if duration:
            # Convert to HH:MM:SS format for HA timer service
            total_seconds = _parse_duration(duration)
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            service_data["duration"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            duration_seconds = total_seconds
        else:
            # Use timer's default duration
            duration_attr = state.attributes.get("duration", "0:00:00")
            duration_seconds = _parse_duration(duration_attr)

        # Start the HA timer
        await hass.services.async_call("timer", "start", service_data)

        # Generate label if not provided
        if not label:
            friendly_name = state.attributes.get("friendly_name", timer_entity)
            label = f"{friendly_name} ({_format_duration(duration_seconds)})"

        # Store label for this timer
        hass.data[DOMAIN].setdefault("timer_labels", {})
        hass.data[DOMAIN]["timer_labels"][timer_entity] = label

        # Show on devices
        await _show_timer_on_devices(
            timer_entity,
            label,
            _format_duration(duration_seconds),
            TIMER_STATE_ACTIVE,
            duration_seconds * 1000
        )

        _LOGGER.info("Started timer %s with duration %s", timer_entity, _format_duration(duration_seconds))

    async def async_pause_timer(call: ServiceCall) -> None:
        """Pause or resume a timer."""
        timer_entity = call.data.get("timer_entity")

        if not timer_entity:
            _LOGGER.error("timer_entity is required for pause_timer")
            return

        state = hass.states.get(timer_entity)
        if state is None:
            _LOGGER.error("Timer entity %s not found", timer_entity)
            return

        current_state = state.state

        if current_state == TIMER_STATE_ACTIVE:
            # Pause the timer
            await hass.services.async_call("timer", "pause", {"entity_id": timer_entity})
            new_state = TIMER_STATE_PAUSED
        elif current_state == TIMER_STATE_PAUSED:
            # Resume the timer
            await hass.services.async_call("timer", "start", {"entity_id": timer_entity})
            new_state = TIMER_STATE_ACTIVE
        else:
            _LOGGER.warning("Timer %s is in state %s, cannot pause/resume", timer_entity, current_state)
            return

        # Get remaining time
        remaining = state.attributes.get("remaining", "0:00:00")
        remaining_seconds = _parse_duration(remaining)

        # Get stored label
        label = hass.data[DOMAIN].get("timer_labels", {}).get(
            timer_entity,
            state.attributes.get("friendly_name", timer_entity)
        )

        # Update devices
        await _show_timer_on_devices(
            timer_entity,
            label,
            _format_duration(remaining_seconds),
            new_state
        )

        _LOGGER.info("Timer %s %s", timer_entity, "paused" if new_state == TIMER_STATE_PAUSED else "resumed")

    async def async_cancel_timer(call: ServiceCall) -> None:
        """Cancel a timer and hide it from Dashie devices."""
        timer_entity = call.data.get("timer_entity")

        if not timer_entity:
            _LOGGER.error("timer_entity is required for cancel_timer")
            return

        # Cancel the HA timer
        await hass.services.async_call("timer", "cancel", {"entity_id": timer_entity})

        # Hide from devices
        await _hide_timer_on_devices(timer_entity)

        # Clean up stored label
        if "timer_labels" in hass.data[DOMAIN]:
            hass.data[DOMAIN]["timer_labels"].pop(timer_entity, None)

        _LOGGER.info("Cancelled timer %s", timer_entity)

    async def async_show_timer(call: ServiceCall) -> None:
        """Show an existing timer on Dashie devices (for timers started elsewhere)."""
        timer_entity = call.data.get("timer_entity")
        label = call.data.get("label", "")

        if not timer_entity:
            _LOGGER.error("timer_entity is required for show_timer")
            return

        state = hass.states.get(timer_entity)
        if state is None:
            _LOGGER.error("Timer entity %s not found", timer_entity)
            return

        current_state = state.state
        if current_state == TIMER_STATE_IDLE:
            _LOGGER.warning("Timer %s is idle, nothing to show", timer_entity)
            return

        # Get remaining time
        remaining = state.attributes.get("remaining", "0:00:00")
        remaining_seconds = _parse_duration(remaining)

        # Generate label if not provided
        if not label:
            friendly_name = state.attributes.get("friendly_name", timer_entity)
            duration_attr = state.attributes.get("duration", "0:00:00")
            duration_seconds = _parse_duration(duration_attr)
            label = f"{friendly_name} ({_format_duration(duration_seconds)})"

        # Store label
        hass.data[DOMAIN].setdefault("timer_labels", {})
        hass.data[DOMAIN]["timer_labels"][timer_entity] = label

        # Show on devices
        timer_state = TIMER_STATE_PAUSED if current_state == TIMER_STATE_PAUSED else TIMER_STATE_ACTIVE
        await _show_timer_on_devices(
            timer_entity,
            label,
            _format_duration(remaining_seconds),
            timer_state
        )

        _LOGGER.info("Showing timer %s on devices", timer_entity)

    # Register all services
    hass.services.async_register(DOMAIN, SERVICE_SEND_COMMAND, async_send_command)
    hass.services.async_register(DOMAIN, SERVICE_LOAD_URL, async_load_url)
    hass.services.async_register(DOMAIN, SERVICE_SPEAK, async_speak)
    hass.services.async_register(DOMAIN, SERVICE_SET_BRIGHTNESS, async_set_brightness)
    hass.services.async_register(DOMAIN, SERVICE_SET_VOLUME, async_set_volume)
    hass.services.async_register(DOMAIN, SERVICE_SHOW_MESSAGE, async_show_message)

    # Timer services
    hass.services.async_register(DOMAIN, SERVICE_START_TIMER, async_start_timer)
    hass.services.async_register(DOMAIN, SERVICE_PAUSE_TIMER, async_pause_timer)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_TIMER, async_cancel_timer)
    hass.services.async_register(DOMAIN, SERVICE_SHOW_TIMER, async_show_timer)

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
        # Timer services
        hass.services.async_remove(DOMAIN, SERVICE_START_TIMER)
        hass.services.async_remove(DOMAIN, SERVICE_PAUSE_TIMER)
        hass.services.async_remove(DOMAIN, SERVICE_CANCEL_TIMER)
        hass.services.async_remove(DOMAIN, SERVICE_SHOW_TIMER)

    return unload_ok
