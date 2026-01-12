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

    def __init__(self, hass: HomeAssistant, host: str, port: int) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Dashie Lite",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"

    async def _async_update_data(self) -> dict:
        """Fetch data from Dashie Lite device."""
        try:
            async with asyncio.timeout(10):
                return await self._fetch_device_info()
        except asyncio.TimeoutError as err:
            raise UpdateFailed(f"Timeout communicating with device at {self.host}") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error communicating with device at {self.host}: {err}") from err

    async def _fetch_device_info(self) -> dict:
        """Fetch device info from the Fully Kiosk API."""
        async with aiohttp.ClientSession() as session:
            url = f"{self.base_url}/?cmd={API_DEVICE_INFO}"
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.json()

    async def send_command(self, command: str, **kwargs) -> bool:
        """Send a command to the Dashie Lite device."""
        try:
            async with aiohttp.ClientSession() as session:
                params = {"cmd": command}
                params.update(kwargs)

                url = f"{self.base_url}/"
                async with session.get(url, params=params) as response:
                    response.raise_for_status()
                    _LOGGER.debug("Command %s sent successfully", command)
                    return True
        except Exception as err:
            _LOGGER.error("Failed to send command %s: %s", command, err)
            return False
