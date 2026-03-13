"""DataUpdateCoordinator for Dashie Lite."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, API_DEVICE_INFO

_LOGGER = logging.getLogger(__name__)

# Backoff configuration for unreachable devices
# Schedule: 15s for first 4 attempts, 30s for next 8, then 2 min
NORMAL_INTERVAL = 15
MEDIUM_BACKOFF = 30
MAX_BACKOFF = 120
MEDIUM_BACKOFF_THRESHOLD = 4   # Switch to 30s after 4 failures
MAX_BACKOFF_THRESHOLD = 12     # Switch to 2 min after 12 failures

# HTTP timeouts for local network devices
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=5, connect=3)


class DashieCoordinator(DataUpdateCoordinator):
    """Coordinator to manage fetching data from Dashie Lite device."""

    def __init__(self, hass: HomeAssistant, host: str, port: int, password: str = "") -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Dashie Lite",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.host = host
        self.port = port
        self.password = password
        self.base_url = f"http://{host}:{port}"
        self._consecutive_failures = 0
        self._session: aiohttp.ClientSession | None = None
        self._is_first_refresh = True
        # Store PIN for unlocking (set when user configures PIN via HA)
        self._stored_pin: str = ""
        # Video feed trigger tracking (centralized via feed registry)
        self._feed_registry = None
        self._tracked_trigger_entities: set[str] = set()
        self._trigger_unsub: list = []
        # Device identity (set by __init__.py from config entry)
        self.device_id: str | None = None

    @property
    def stored_pin(self) -> str:
        """Return the stored PIN for unlocking."""
        return self._stored_pin

    def set_stored_pin(self, pin: str) -> None:
        """Store the PIN for use when unlocking."""
        self._stored_pin = pin
        _LOGGER.debug("Stored PIN updated")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create a reusable HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=HTTP_TIMEOUT)
        return self._session

    async def async_shutdown(self) -> None:
        """Close the HTTP session on shutdown."""
        self._unsubscribe_triggers()
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        await super().async_shutdown()

    def update_local_data(self, **kwargs) -> None:
        """Optimistically update local data cache for immediate UI feedback.

        This allows entities to update the coordinator's data immediately
        after sending a command, rather than waiting for the next poll.
        The next poll will overwrite with the actual device state.
        """
        if self.data:
            self.data.update(kwargs)
            # Notify listeners that data has changed
            self.async_set_updated_data(self.data)

    def _apply_backoff(self) -> None:
        """Apply step-based backoff after a failure.

        Schedule: 15s for first 4 attempts, 30s for next 8, then 2 min.
        """
        self._consecutive_failures += 1

        # Determine backoff interval based on failure count
        if self._consecutive_failures <= MEDIUM_BACKOFF_THRESHOLD:
            # First 4 failures: stay at normal 15s interval
            new_interval = NORMAL_INTERVAL
        elif self._consecutive_failures <= MAX_BACKOFF_THRESHOLD:
            # Failures 5-12: back off to 30s
            new_interval = MEDIUM_BACKOFF
        else:
            # After 12 failures: back off to 2 minutes
            new_interval = MAX_BACKOFF

        # Update the coordinator's polling interval
        self.update_interval = timedelta(seconds=new_interval)

        # Only log at threshold transitions or every 10 failures to reduce log spam
        if (self._consecutive_failures == 1 or
            self._consecutive_failures == MEDIUM_BACKOFF_THRESHOLD + 1 or
            self._consecutive_failures == MAX_BACKOFF_THRESHOLD + 1 or
            self._consecutive_failures % 10 == 0):
            _LOGGER.warning(
                "Device %s unreachable (attempt #%d), polling interval: %ds",
                self.host, self._consecutive_failures, new_interval
            )

    def _reset_backoff(self) -> None:
        """Reset backoff to normal polling after successful connection."""
        if self._consecutive_failures > 0:
            _LOGGER.info(
                "Reconnected to Dashie device at %s after %d failures",
                self.host, self._consecutive_failures
            )
        self._consecutive_failures = 0
        self.update_interval = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

    async def _async_update_data(self) -> dict:
        """Fetch data from Dashie Lite device."""
        try:
            async with asyncio.timeout(10):
                data = await self._fetch_device_info()
                # Reset failure counter and backoff on success
                self._reset_backoff()
                # Update video feed trigger subscriptions if entity list changed
                self._update_trigger_subscriptions(data)
                return data
        except asyncio.TimeoutError as err:
            self._apply_backoff()
            raise UpdateFailed(f"Timeout communicating with device at {self.host}") from err
        except aiohttp.ClientError as err:
            self._apply_backoff()
            raise UpdateFailed(f"Error communicating with device at {self.host}: {err}") from err
        except Exception as err:
            self._apply_backoff()
            _LOGGER.error("Unexpected error with device at %s: %s", self.host, err)
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def _fetch_device_info(self) -> dict:
        """Fetch device info from the device API."""
        session = await self._get_session()

        url = f"{self.base_url}/?cmd={API_DEVICE_INFO}&type=json"
        if self.password:
            url += f"&password={self.password}"

        async with session.get(url) as response:
            response.raise_for_status()
            data = await response.json()

            # Check for error response
            if data.get("status") == "ERROR":
                raise UpdateFailed(data.get("message", "Unknown error"))

        # On first refresh, skip RTSP calls to speed up initial connection.
        # RTSP data will be populated on the next poll cycle (5s later).
        if self._is_first_refresh:
            self._is_first_refresh = False
            # Carry over rtspConfig from deviceInfo if present
            if "rtspConfig" in data:
                data["rtsp_config"] = data["rtspConfig"]
            return data

        # Fetch RTSP status and config in parallel
        rtsp_status_task = self._fetch_rtsp_status(session)
        rtsp_config_task = (
            self._fetch_rtsp_config(session)
            if "rtspConfig" not in data
            else None
        )

        tasks = [rtsp_status_task]
        if rtsp_config_task:
            tasks.append(rtsp_config_task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # RTSP status
        rtsp_status = results[0]
        if isinstance(rtsp_status, dict):
            data["rtsp_status"] = rtsp_status

        # RTSP config - prefer from deviceInfo, fallback to API
        if "rtspConfig" in data:
            data["rtsp_config"] = data["rtspConfig"]
        elif len(results) > 1:
            rtsp_config = results[1]
            if isinstance(rtsp_config, dict):
                data["rtsp_config"] = rtsp_config

        return data

    async def _fetch_rtsp_status(self, session: aiohttp.ClientSession) -> dict | None:
        """Fetch RTSP stream status from the device."""
        try:
            url = f"{self.base_url}/?cmd=getRtspStatus"
            if self.password:
                url += f"&password={self.password}"

            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("status") != "ERROR":
                        return data
        except Exception as err:
            _LOGGER.debug("Could not fetch RTSP status: %s", err)
        return None

    async def _fetch_rtsp_config(self, session: aiohttp.ClientSession) -> dict | None:
        """Fetch RTSP configuration from the device."""
        try:
            url = f"{self.base_url}/?cmd=getRtspConfig"
            if self.password:
                url += f"&password={self.password}"

            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("status") != "ERROR":
                        return data
        except Exception as err:
            _LOGGER.debug("Could not fetch RTSP config: %s", err)
        return None

    # ── Centralized Video Feed Trigger Tracking ─────────────────────

    def set_feed_registry(self, registry) -> None:
        """Set the feed registry and subscribe to trigger entities."""
        self._feed_registry = registry
        self.refresh_feed_triggers()

    @callback
    def refresh_feed_triggers(self) -> None:
        """Refresh trigger subscriptions from the feed registry.

        Called when feeds are created/updated/deleted. Only the first
        coordinator to call this actually subscribes (triggers are global,
        but each coordinator handles pushing to its own device).
        """
        if not self._feed_registry:
            return

        new_entities = self._feed_registry.get_all_trigger_entities()
        if new_entities == self._tracked_trigger_entities:
            return

        _LOGGER.info(
            "Feed trigger entities changed for %s: %s -> %s",
            self.host, self._tracked_trigger_entities, new_entities,
        )

        self._unsubscribe_triggers()
        self._tracked_trigger_entities = new_entities

        if not new_entities:
            return

        unsub = async_track_state_change_event(
            self.hass, list(new_entities), self._handle_feed_trigger
        )
        self._trigger_unsub.append(unsub)
        _LOGGER.info(
            "Subscribed to %d feed trigger entities for %s",
            len(new_entities), self.host,
        )

    # Keep legacy method for backward compat during transition
    @callback
    def _update_trigger_subscriptions(self, data: dict) -> None:
        """Legacy: update from deviceInfo. No-op if feed registry is active."""
        if self._feed_registry:
            return  # Triggers managed by registry now

        new_entities = set(data.get("videoFeedTriggerEntities", []))
        if new_entities == self._tracked_trigger_entities:
            return

        self._unsubscribe_triggers()
        self._tracked_trigger_entities = new_entities

        if not new_entities:
            return

        unsub = async_track_state_change_event(
            self.hass, list(new_entities), self._handle_legacy_trigger
        )
        self._trigger_unsub.append(unsub)

    def _unsubscribe_triggers(self) -> None:
        """Remove all trigger state change subscriptions."""
        for unsub in self._trigger_unsub:
            unsub()
        self._trigger_unsub.clear()

    @callback
    def _handle_feed_trigger(self, event: Event) -> None:
        """Handle trigger from centralized feed registry.

        Looks up which feeds match, checks if this device subscribes
        with trigger or trigger_alert mode, and pushes accordingly.
        """
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return

        entity_id = new_state.entity_id
        state_val = new_state.state
        old_val = old_state.state if old_state else None

        if old_val == state_val:
            return

        if not self._feed_registry or not self.device_id:
            return

        # Find matching feeds
        matching_feeds = self._feed_registry.get_feeds_for_trigger(entity_id, state_val)
        if not matching_feeds:
            return

        for feed in matching_feeds:
            feed_id = feed["id"]
            # Check this device's subscription mode
            sub = self._feed_registry.get_subscription(self.device_id)
            mode = sub.get("feed_modes", {}).get(
                feed_id, feed.get("default_mode", "subscribed")
            )

            if mode not in ("trigger", "trigger_alert"):
                continue

            _LOGGER.debug(
                "Feed trigger: %s -> feed %s, pushing to %s (mode=%s)",
                entity_id, feed_id, self.host, mode,
            )
            self.hass.async_create_task(
                self.send_command(
                    "videoFeedTrigger",
                    entityId=entity_id,
                    state=state_val,
                    feedId=feed_id,
                    feedLabel=feed.get("label", ""),
                    cameraEntityId=feed.get("camera_entity_id", ""),
                    mode=mode,
                    autoDismissSeconds=str(feed.get("auto_dismiss_seconds", 30)),
                    continueWhileActive=str(feed.get("continue_while_active", True)).lower(),
                    alertSound=feed.get("alert_sound", ""),
                    streamSourceType=feed.get("stream_source_type", "entity"),
                    streamSourceUrl=feed.get("stream_source_url", ""),
                )
            )

    @callback
    def _handle_legacy_trigger(self, event: Event) -> None:
        """Legacy trigger handler: push directly to this device only."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None:
            return

        entity_id = new_state.entity_id
        state_val = new_state.state
        old_val = old_state.state if old_state else None

        if old_val == state_val:
            return

        _LOGGER.debug(
            "Legacy video feed trigger: %s changed %s -> %s, pushing to %s",
            entity_id, old_val, state_val, self.host
        )
        self.hass.async_create_task(
            self.send_command("videoFeedTrigger", entityId=entity_id, state=state_val)
        )

    async def send_command(self, command: str, **kwargs) -> bool:
        """Send a command to the Dashie Lite device."""
        try:
            session = await self._get_session()
            params = {"cmd": command}
            if self.password:
                params["password"] = self.password
            params.update(kwargs)

            url = f"{self.base_url}/"
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                result = await response.json()

                # Check for error response
                if result.get("status") == "ERROR":
                    _LOGGER.error("Command %s failed: %s", command, result.get("message"))
                    return False

                _LOGGER.debug("Command %s sent successfully", command)
                return True
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout sending command %s to %s", command, self.host)
            return False
        except aiohttp.ClientError as err:
            _LOGGER.error("Connection error sending command %s: %s", command, err)
            return False
        except Exception as err:
            _LOGGER.error("Failed to send command %s: %s", command, err)
            return False
