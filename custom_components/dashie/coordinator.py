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

    async def _async_update_data(self) -> dict:
        """Fetch data from Dashie Lite device."""
        try:
            async with asyncio.timeout(15):  # Increased timeout
                data = await self._fetch_device_info()
                # Reset failure counter on success
                if self._consecutive_failures > 0:
                    _LOGGER.info("Reconnected to Dashie device at %s after %d failures",
                                self.host, self._consecutive_failures)
                self._consecutive_failures = 0
                return data
        except asyncio.TimeoutError as err:
            self._consecutive_failures += 1
            _LOGGER.warning("Timeout (#%d) communicating with device at %s",
                          self._consecutive_failures, self.host)
            raise UpdateFailed(f"Timeout communicating with device at {self.host}") from err
        except aiohttp.ClientError as err:
            self._consecutive_failures += 1
            _LOGGER.warning("Connection error (#%d) with device at %s: %s",
                          self._consecutive_failures, self.host, err)
            raise UpdateFailed(f"Error communicating with device at {self.host}: {err}") from err
        except Exception as err:
            self._consecutive_failures += 1
            _LOGGER.error("Unexpected error (#%d) with device at %s: %s",
                         self._consecutive_failures, self.host, err)
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

                return data

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
