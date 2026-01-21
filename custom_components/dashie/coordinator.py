"""DataUpdateCoordinator for Dashie Lite."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
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
        # Store PIN for unlocking (set when user configures PIN via HA)
        self._stored_pin: str = ""

    @property
    def stored_pin(self) -> str:
        """Return the stored PIN for unlocking."""
        return self._stored_pin

    def set_stored_pin(self, pin: str) -> None:
        """Store the PIN for use when unlocking."""
        self._stored_pin = pin
        _LOGGER.debug("Stored PIN updated")

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
            async with asyncio.timeout(15):  # Increased timeout
                data = await self._fetch_device_info()
                # Reset failure counter and backoff on success
                self._reset_backoff()
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
        """Fetch device info from the Fully Kiosk API."""
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = f"{self.base_url}/?cmd={API_DEVICE_INFO}&type=json"
            if self.password:
                url += f"&password={self.password}"

            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.json()

                # Check for error response
                if data.get("status") == "ERROR":
                    raise UpdateFailed(data.get("message", "Unknown error"))

            # Also fetch RTSP status for camera entity
            rtsp_data = await self._fetch_rtsp_status(session)
            if rtsp_data:
                data["rtsp_status"] = rtsp_data

            # RTSP config - prefer from deviceInfo (rtspConfig), fallback to separate API call
            if "rtspConfig" in data:
                # deviceInfo includes rtspConfig directly (camelCase)
                data["rtsp_config"] = data["rtspConfig"]
            else:
                # Fallback: fetch from separate getRtspConfig API
                rtsp_config = await self._fetch_rtsp_config(session)
                if rtsp_config:
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

    async def send_command(self, command: str, **kwargs) -> bool:
        """Send a command to the Dashie Lite device."""
        try:
            timeout = aiohttp.ClientTimeout(total=10, connect=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
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
