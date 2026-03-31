"""Dashie integration for Home Assistant."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import timedelta
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.event import async_track_time_interval

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
from .const import CONF_DEVICE_ID
from .coordinator import DashieCoordinator
from .feed_registry import FeedRegistry, register_feed_registry_views
from .media_api import register_media_api_views
from .music_token_store import MusicTokenStore, register_music_token_views
from .music_relay import register_music_relay_views
from .hidden_speakers_store import HiddenSpeakersStore, register_hidden_speakers_views
from .sensor_push import register_sensor_push_views
from .stream_multiplexer import StreamMultiplexer, register_stream_multiplexer_views
from .device_name_views import register_device_name_views
from .stream_proxy import register_stream_proxy_views

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.UPDATE,
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

# Timer constants
MAX_TIMERS = 3
TIMER_STATE_ACTIVE = "active"
TIMER_STATE_PAUSED = "paused"
TIMER_TICK_INTERVAL = timedelta(seconds=1)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Track if views are registered (only register once)
_media_api_registered = False
_stream_proxy_registered = False
_feed_registry_registered = False
_multiplexer_registered = False
_music_token_registered = False
_music_relay_registered = False
_sensor_push_registered = False
_device_name_registered = False


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Dashie integration."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dashie Lite from a config entry."""
    global _media_api_registered, _stream_proxy_registered
    global _feed_registry_registered, _multiplexer_registered, _sensor_push_registered
    global _music_token_registered, _music_relay_registered, _device_name_registered

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    password = entry.data.get(CONF_PASSWORD, "")

    coordinator = DashieCoordinator(hass, host, port, password, config_entry=entry)
    # Store device_id for feed subscription lookups
    coordinator.device_id = entry.data.get(CONF_DEVICE_ID)
    # Use async_refresh() instead of async_config_entry_first_refresh() so that
    # offline devices don't cause HA to retry setup (which recreates the coordinator
    # and resets our backoff counter). The entry stays loaded even if the device is
    # temporarily unreachable.
    await coordinator.async_refresh()

    # Auto-remove ghost entries: if the first poll failed and this entry has
    # no entities in the entity registry, it's likely an orphaned config entry
    # from a failed deletion. Disabled entities still count — we only remove
    # entries with zero entities.
    if not coordinator.last_update_success:
        entity_registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        if not entities:
            _LOGGER.warning(
                "Removing orphaned Dashie config entry for %s (%s) — "
                "device is unreachable and has no registered entities",
                entry.title, host,
            )
            # Mark as ghost so async_unload_entry skips platform unload
            hass.data.setdefault(DOMAIN, {})
            hass.data[DOMAIN][f"{entry.entry_id}_ghost"] = True
            hass.async_create_task(
                hass.config_entries.async_remove(entry.entry_id)
            )
            return True

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (only once)
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        await _async_register_services(hass)

    # Initialize feed registry (only once)
    if "feed_registry" not in hass.data[DOMAIN]:
        registry = FeedRegistry(hass)
        await registry.async_load()
        hass.data[DOMAIN]["feed_registry"] = registry
        _LOGGER.info("Initialized Dashie feed registry")

    # Initialize stream multiplexer (only once)
    if "stream_multiplexer" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["stream_multiplexer"] = StreamMultiplexer(hass)
        _LOGGER.info("Initialized Dashie stream multiplexer")

    # Register HTTP views (only once)
    if not _media_api_registered:
        register_media_api_views(hass)
        _media_api_registered = True
        _LOGGER.info("Registered Dashie Media API views")

    if not _stream_proxy_registered:
        register_stream_proxy_views(hass)
        _stream_proxy_registered = True
        _LOGGER.info("Registered Dashie MJPEG stream proxy")

    if not _feed_registry_registered:
        register_feed_registry_views(hass)
        _feed_registry_registered = True
        _LOGGER.info("Registered Dashie feed registry views")

    # Initialize music token store (only once)
    if "music_token_store" not in hass.data[DOMAIN]:
        music_store = MusicTokenStore(hass)
        await music_store.async_load()
        hass.data[DOMAIN]["music_token_store"] = music_store
        _LOGGER.info("Initialized Dashie music token store")

    if not _music_token_registered:
        register_music_token_views(hass)
        _music_token_registered = True
        _LOGGER.info("Registered Dashie music token views")

    # Initialize hidden speakers store (only once)
    if "hidden_speakers_store" not in hass.data[DOMAIN]:
        hidden_store = HiddenSpeakersStore(hass)
        await hidden_store.async_load()
        hass.data[DOMAIN]["hidden_speakers_store"] = hidden_store
        _LOGGER.info("Initialized Dashie hidden speakers store")

    if not getattr(register_hidden_speakers_views, '_registered', False):
        register_hidden_speakers_views(hass)
        register_hidden_speakers_views._registered = True
        _LOGGER.info("Registered Dashie hidden speakers views")

    if not _music_relay_registered:
        register_music_relay_views(hass)
        _music_relay_registered = True
        _LOGGER.info("Registered Dashie music relay views")

    if not _multiplexer_registered:
        register_stream_multiplexer_views(hass)
        _multiplexer_registered = True
        _LOGGER.info("Registered Dashie stream multiplexer views")

    if not _sensor_push_registered:
        register_sensor_push_views(hass)
        _sensor_push_registered = True
        _LOGGER.info("Registered Dashie sensor push endpoint")

    if not _device_name_registered:
        register_device_name_views(hass)
        _device_name_registered = True
        _LOGGER.info("Registered Dashie device name views")

    # Set up centralized feed trigger subscriptions
    registry = hass.data[DOMAIN]["feed_registry"]
    coordinator.set_feed_registry(registry)

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

    # --- Internal Timer Management ---
    # Timers are managed internally (not using HA timer helpers)
    # Structure: hass.data[DOMAIN]["timers"] = {
    #   "timer_id": {
    #     "id": "uuid",
    #     "slot": 1-3,
    #     "label": "Timer 1 (5:00)",
    #     "duration_seconds": 300,
    #     "remaining_seconds": 182,
    #     "state": "active" | "paused" | "completed",
    #     "started_at": timestamp,
    #     "paused_at": timestamp or None,
    #   }
    # }

    def _get_timers() -> dict:
        """Get the timers dict, initializing if needed."""
        hass.data[DOMAIN].setdefault("timers", {})
        return hass.data[DOMAIN]["timers"]

    def _format_duration(seconds: int) -> str:
        """Format seconds into display format (m:ss or h:mm:ss)."""
        if seconds < 0:
            seconds = 0
        if seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}:{secs:02d}"
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours}:{minutes:02d}:{secs:02d}"

    def _format_duration_label(seconds: int) -> str:
        """Format seconds into human-readable label (e.g., '5 min', '1 hr 30 min')."""
        if seconds < 60:
            return f"{seconds} sec"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} min"
        hours = minutes // 60
        mins = minutes % 60
        if mins == 0:
            return f"{hours} hr"
        return f"{hours} hr {mins} min"

    def _parse_duration(duration_str) -> int:
        """Parse duration string to seconds. Accepts: 300, '5:00', '1:30:00', '5 minutes'."""
        if isinstance(duration_str, (int, float)):
            return int(duration_str)

        duration_str = str(duration_str).strip().lower()

        # Handle "X minutes", "X min", "X seconds", "X sec", "X hours", "X hr"
        import re
        match = re.match(r'^(\d+)\s*(hours?|hr|minutes?|min|seconds?|sec)?$', duration_str)
        if match:
            value = int(match.group(1))
            unit = match.group(2) or 'sec'
            if unit.startswith('hour') or unit == 'hr':
                return value * 3600
            elif unit.startswith('min'):
                return value * 60
            else:
                return value

        # Handle HH:MM:SS or MM:SS format
        parts = duration_str.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        else:
            return int(duration_str)

    def _find_available_slot() -> int | None:
        """Find the first available timer slot (1-3)."""
        timers = _get_timers()
        used_slots = {t["slot"] for t in timers.values()}
        for slot in range(1, MAX_TIMERS + 1):
            if slot not in used_slots:
                return slot
        return None

    def _get_timer_by_slot(slot: int) -> dict | None:
        """Get timer by slot number."""
        for timer in _get_timers().values():
            if timer["slot"] == slot:
                return timer
        return None

    def _calculate_remaining(timer: dict) -> int:
        """Calculate remaining seconds for a timer."""
        if timer["state"] == "paused":
            return timer["remaining_seconds"]
        elif timer["state"] == "active":
            elapsed = time.time() - timer["started_at"]
            remaining = timer["duration_seconds"] - int(elapsed)
            return max(0, remaining)
        return 0

    async def _send_timer_to_devices(timer: dict, action: str = "update") -> None:
        """Send timer state to all Dashie devices."""
        remaining = _calculate_remaining(timer)
        for coordinator in _get_all_coordinators():
            await coordinator.send_command(
                "showTimer",
                timerId=timer["id"],
                slot=str(timer["slot"]),
                label=timer["label"],
                remaining=_format_duration(remaining),
                remainingSeconds=str(remaining),
                state=timer["state"],
                action=action
            )

    async def _hide_timer_from_devices(timer_id: str, slot: int) -> None:
        """Hide timer from all Dashie devices."""
        for coordinator in _get_all_coordinators():
            await coordinator.send_command(
                "hideTimer",
                timerId=timer_id,
                slot=str(slot)
            )

    async def _timer_tick(now) -> None:
        """Called every second to update active timers."""
        timers = _get_timers()
        completed_ids = []

        for timer_id, timer in timers.items():
            if timer["state"] == "active":
                remaining = _calculate_remaining(timer)
                if remaining <= 0:
                    # Timer completed
                    timer["state"] = "completed"
                    timer["remaining_seconds"] = 0
                    completed_ids.append(timer_id)
                    await _send_timer_to_devices(timer, action="completed")
                    _LOGGER.info("Timer %s completed", timer["label"])
                else:
                    # Send tick update to devices
                    await _send_timer_to_devices(timer, action="tick")

        # Remove completed timers after a short delay (let device show completion)
        for timer_id in completed_ids:
            # Keep completed timer for 5 seconds then remove
            async def remove_after_delay(tid):
                import asyncio
                await asyncio.sleep(5)
                if tid in _get_timers():
                    timer = _get_timers().pop(tid)
                    await _hide_timer_from_devices(tid, timer["slot"])
            hass.async_create_task(remove_after_delay(timer_id))

    # Start the timer tick interval
    hass.data[DOMAIN]["timer_unsub"] = async_track_time_interval(
        hass, _timer_tick, TIMER_TICK_INTERVAL
    )

    async def async_start_timer(call: ServiceCall) -> None:
        """Start a new timer."""
        duration = call.data.get("duration")
        label = call.data.get("label", "")

        if not duration:
            _LOGGER.error("duration is required for start_timer")
            return

        # Parse duration
        duration_seconds = _parse_duration(duration)
        if duration_seconds <= 0:
            _LOGGER.error("Invalid duration: %s", duration)
            return

        # Find available slot
        slot = _find_available_slot()
        if slot is None:
            _LOGGER.warning("All timer slots are in use (max %d)", MAX_TIMERS)
            # Notify devices that no slot is available
            for coordinator in _get_all_coordinators():
                await coordinator.send_command(
                    "setOverlayMessage",
                    text="All timer slots in use",
                    duration="3000"
                )
            return

        # Create timer
        timer_id = str(uuid.uuid4())
        if not label:
            label = f"Timer {slot} ({_format_duration_label(duration_seconds)})"

        timer = {
            "id": timer_id,
            "slot": slot,
            "label": label,
            "duration_seconds": duration_seconds,
            "remaining_seconds": duration_seconds,
            "state": TIMER_STATE_ACTIVE,
            "started_at": time.time(),
            "paused_at": None,
        }

        _get_timers()[timer_id] = timer
        await _send_timer_to_devices(timer, action="start")
        _LOGGER.info("Started timer %s (slot %d) for %s", label, slot, _format_duration(duration_seconds))

    async def async_pause_timer(call: ServiceCall) -> None:
        """Pause or resume a timer."""
        slot = call.data.get("slot")
        timer_id = call.data.get("timer_id")

        # Find the timer
        timer = None
        if timer_id:
            timer = _get_timers().get(timer_id)
        elif slot:
            timer = _get_timer_by_slot(int(slot))
        else:
            # If only one timer, use that
            timers = _get_timers()
            if len(timers) == 1:
                timer = list(timers.values())[0]
            elif len(timers) > 1:
                _LOGGER.warning("Multiple timers active, specify slot or timer_id")
                return

        if not timer:
            _LOGGER.warning("Timer not found")
            return

        if timer["state"] == TIMER_STATE_ACTIVE:
            # Pause: save remaining time
            timer["remaining_seconds"] = _calculate_remaining(timer)
            timer["state"] = TIMER_STATE_PAUSED
            timer["paused_at"] = time.time()
            _LOGGER.info("Paused timer %s", timer["label"])
        elif timer["state"] == TIMER_STATE_PAUSED:
            # Resume: restart from remaining time
            timer["state"] = TIMER_STATE_ACTIVE
            timer["started_at"] = time.time()
            timer["duration_seconds"] = timer["remaining_seconds"]
            timer["paused_at"] = None
            _LOGGER.info("Resumed timer %s", timer["label"])
        else:
            _LOGGER.warning("Timer %s is in state %s, cannot pause/resume", timer["label"], timer["state"])
            return

        await _send_timer_to_devices(timer, action="pause" if timer["state"] == TIMER_STATE_PAUSED else "resume")

    async def async_cancel_timer(call: ServiceCall) -> None:
        """Cancel a timer."""
        slot = call.data.get("slot")
        timer_id = call.data.get("timer_id")

        # Find the timer
        timer = None
        tid = None
        if timer_id:
            timer = _get_timers().get(timer_id)
            tid = timer_id
        elif slot:
            timer = _get_timer_by_slot(int(slot))
            if timer:
                tid = timer["id"]
        else:
            # If only one timer, use that
            timers = _get_timers()
            if len(timers) == 1:
                tid = list(timers.keys())[0]
                timer = timers[tid]
            elif len(timers) > 1:
                _LOGGER.warning("Multiple timers active, specify slot or timer_id")
                return

        if not timer or not tid:
            _LOGGER.warning("Timer not found")
            return

        # Remove timer and notify devices
        slot_num = timer["slot"]
        label = timer["label"]
        _get_timers().pop(tid, None)
        await _hide_timer_from_devices(tid, slot_num)
        _LOGGER.info("Cancelled timer %s", label)

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

    _LOGGER.info("Registered Dashie services")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Ghost entries (auto-removed orphans) never set up platforms — skip unload
    ghost_key = f"{entry.entry_id}_ghost"
    is_ghost = hass.data.get(DOMAIN, {}).pop(ghost_key, False)

    # Always shut down the coordinator first — even if platform unload fails,
    # we must stop polling and close the HTTP session to prevent ghost devices.
    coordinator: DashieCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator:
        await coordinator.async_shutdown()

    if not is_ghost:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if not unload_ok:
            _LOGGER.warning(
                "Platform unload incomplete for %s (%s), cleaning up anyway",
                entry.title, entry.data.get(CONF_HOST),
            )

    # Always remove the coordinator from hass.data regardless of platform unload
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)

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
        # Stop timer tick interval
        if "timer_unsub" in hass.data[DOMAIN]:
            hass.data[DOMAIN]["timer_unsub"]()
        # Shut down stream multiplexer
        multiplexer = hass.data[DOMAIN].pop("stream_multiplexer", None)
        if multiplexer:
            await multiplexer.async_shutdown()

    # Always return True so HA completes the deletion and removes from storage
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up after a config entry is fully removed.

    Called by HA after async_unload_entry succeeds and the entry is deleted
    from storage. Belt-and-suspenders cleanup for any remaining state.
    """
    device_id = entry.data.get(CONF_DEVICE_ID)
    host = entry.data.get(CONF_HOST)

    # Clean up orphaned feed registry subscriptions for this device
    registry = hass.data.get(DOMAIN, {}).get("feed_registry")
    if registry and device_id:
        sub = registry.get_subscription(device_id)
        if sub and sub.get("feed_modes"):
            _LOGGER.info(
                "Removing feed subscriptions for deleted device %s (%s)",
                device_id, host,
            )
            await registry.async_remove_subscription(device_id)

    _LOGGER.info("Cleaned up removed Dashie device %s (%s)", device_id, host)
