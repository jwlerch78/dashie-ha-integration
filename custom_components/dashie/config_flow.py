"""Config flow for Dashie Lite integration."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import ssdp
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, DEFAULT_PORT, CONF_DEVICE_ID, CONF_DEVICE_NAME

_LOGGER = logging.getLogger(__name__)


class DashieConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dashie Lite."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._password: str = ""
        self._device_info: dict | None = None

    async def async_step_ssdp(self, discovery_info: ssdp.SsdpServiceInfo) -> FlowResult:
        """Handle SSDP discovery."""
        _LOGGER.debug("SSDP discovery received: %s", discovery_info)

        # Parse location URL to get host and port
        location = discovery_info.ssdp_location
        if not location:
            return self.async_abort(reason="no_location")

        parsed = urlparse(location)
        self._host = parsed.hostname
        self._port = parsed.port or DEFAULT_PORT

        # Get device name and HA URL from SSDP headers
        device_name = discovery_info.upnp.get("X-DASHIE-NAME", "Dashie Lite")
        configured_ha_url = discovery_info.upnp.get("X-DASHIE-HA-URL")

        # Optional: Check if this tablet is configured to connect to THIS HA instance
        if configured_ha_url:
            _LOGGER.debug("Tablet configured for HA URL: %s", configured_ha_url)

        # Try to fetch device info without password first
        try:
            self._device_info = await self._fetch_device_info()
            device_id = self._device_info.get("deviceID")

            if device_id:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})

                self._device_info["deviceName"] = device_name
                self.context["title_placeholders"] = {"name": device_name}
                return await self.async_step_confirm()

        except aiohttp.ClientResponseError as err:
            if err.status == 401:
                # Password required - go to password step
                self.context["title_placeholders"] = {"name": device_name}
                return await self.async_step_password()
            raise
        except Exception as err:
            _LOGGER.error("Failed to fetch device info: %s", err)
            return self.async_abort(reason="cannot_connect")

        return self.async_abort(reason="no_device_id")

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle password entry for discovered device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._password = user_input.get(CONF_PASSWORD, "")

            try:
                self._device_info = await self._fetch_device_info()
                device_id = self._device_info.get("deviceID")

                if device_id:
                    await self.async_set_unique_id(device_id)
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=self._device_info.get("deviceName", "Dashie Lite"),
                        data={
                            CONF_HOST: self._host,
                            CONF_PORT: self._port,
                            CONF_PASSWORD: self._password,
                            CONF_DEVICE_ID: device_id,
                            CONF_DEVICE_NAME: self._device_info.get("deviceName"),
                        },
                    )
                else:
                    errors["base"] = "no_device_id"
            except aiohttp.ClientResponseError as err:
                if err.status == 401:
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="password",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "host": self._host,
            },
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm the discovered device."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._device_info.get("deviceName", "Dashie Lite"),
                data={
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_PASSWORD: self._password,
                    CONF_DEVICE_ID: self._device_info.get("deviceID"),
                    CONF_DEVICE_NAME: self._device_info.get("deviceName"),
                },
            )

        device_name = self._device_info.get("deviceName", "Dashie Lite")
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": device_name,
                "host": self._host,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input.get(CONF_PORT, DEFAULT_PORT)
            self._password = user_input.get(CONF_PASSWORD, "")

            try:
                self._device_info = await self._fetch_device_info()
                device_id = self._device_info.get("deviceID")

                if device_id:
                    await self.async_set_unique_id(device_id)
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=self._device_info.get("deviceName", "Dashie Lite"),
                        data={
                            CONF_HOST: self._host,
                            CONF_PORT: self._port,
                            CONF_PASSWORD: self._password,
                            CONF_DEVICE_ID: device_id,
                            CONF_DEVICE_NAME: self._device_info.get("deviceName"),
                        },
                    )
                else:
                    errors["base"] = "no_device_id"
            except aiohttp.ClientResponseError as err:
                if err.status == 401:
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                    vol.Optional(CONF_PASSWORD, default=""): str,
                }
            ),
            errors=errors,
        )

    async def _fetch_device_info(self) -> dict:
        """Fetch device info from the device."""
        async with aiohttp.ClientSession() as session:
            url = f"http://{self._host}:{self._port}/?cmd=deviceInfo&type=json"
            if self._password:
                url += f"&password={self._password}"

            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()
                data = await response.json()

                # Check for error response (API returns 200 with error in body)
                if data.get("status") == "ERROR":
                    if "password" in data.get("message", "").lower():
                        raise aiohttp.ClientResponseError(
                            request_info=response.request_info,
                            history=(),
                            status=401,
                            message="Invalid password",
                        )
                    raise Exception(data.get("message", "Unknown error"))

                return data
