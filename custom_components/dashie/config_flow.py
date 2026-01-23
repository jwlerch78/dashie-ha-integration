"""Config flow for Dashie integration."""
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

from .const import (
    DOMAIN,
    DEFAULT_PORT,
    DEFAULT_MEDIA_FOLDER,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_MEDIA_FOLDER,
)

_LOGGER = logging.getLogger(__name__)


class DashieConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dashie."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._password: str = ""
        self._device_info: dict | None = None
        self._is_dashie_lite: bool = False

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

        # Check if we already have an entry with this host IP
        # This catches devices that were configured before we had proper unique_id handling,
        # or where unique_id changed (e.g., device_id vs ssdp_uuid mismatch)
        for entry in self._async_current_entries():
            if entry.data.get(CONF_HOST) == self._host:
                _LOGGER.debug("Device at %s already configured (entry: %s)", self._host, entry.entry_id)
                return self.async_abort(reason="already_configured")

        # Get device name and HA URL from SSDP headers
        # Custom X- headers are in ssdp_headers, not upnp
        ssdp_headers = discovery_info.ssdp_headers or {}
        device_name = ssdp_headers.get("X-DASHIE-NAME") or ssdp_headers.get("x-dashie-name") or "Dashie"
        configured_ha_url = ssdp_headers.get("X-DASHIE-HA-URL") or ssdp_headers.get("x-dashie-ha-url")

        # Detect if this is a Dashie Lite device based on service type
        ssdp_st = discovery_info.ssdp_st or ""
        self._is_dashie_lite = "DashieLite" in ssdp_st

        # Extract UUID from USN header (format: uuid:xxx::urn:dashie:service:DashieLite:1 or Dashie:1)
        usn = discovery_info.ssdp_usn or ""
        ssdp_uuid = None
        if usn.startswith("uuid:"):
            # Extract just the UUID part before the "::" separator
            ssdp_uuid = usn.split("::")[0].replace("uuid:", "")

        # Set unique_id early from SSDP UUID to prevent duplicate discoveries
        if ssdp_uuid:
            await self.async_set_unique_id(ssdp_uuid)
            self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})

        # Optional: Check if this tablet is configured to connect to THIS HA instance
        if configured_ha_url:
            _LOGGER.debug("Tablet configured for HA URL: %s", configured_ha_url)

        # Try to fetch device info without password first
        try:
            self._device_info = await self._fetch_device_info()
            device_id = self._device_info.get("deviceID")

            if device_id:
                # Update unique_id with actual device_id (more reliable than UUID)
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})

                self._device_info["deviceName"] = device_name
                self.context["title_placeholders"] = {"name": device_name}
                return await self.async_step_confirm()

        except aiohttp.ClientResponseError as err:
            if err.status == 401:
                # Password required - go to password step
                # Note: unique_id was already set from SSDP UUID above
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

                    base_name = self._device_info.get("deviceName", "Dashie")
                    display_name = self._get_display_name(base_name)

                    return self.async_create_entry(
                        title=display_name,
                        data={
                            CONF_HOST: self._host,
                            CONF_PORT: self._port,
                            CONF_PASSWORD: self._password,
                            CONF_DEVICE_ID: device_id,
                            CONF_DEVICE_NAME: display_name,
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
        """Confirm the discovered device and optionally set password."""
        base_name = self._device_info.get("deviceName", "Dashie")
        display_name = self._get_display_name(base_name)

        if user_input is not None:
            # Get optional password from form
            password = user_input.get(CONF_PASSWORD, "")

            return self.async_create_entry(
                title=display_name,
                data={
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_PASSWORD: password,
                    CONF_DEVICE_ID: self._device_info.get("deviceID"),
                    CONF_DEVICE_NAME: display_name,
                },
            )

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_PASSWORD, default=""): str,
                }
            ),
            description_placeholders={
                "name": display_name,
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
                        title=self._device_info.get("deviceName", "Dashie"),
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

    def _get_display_name(self, base_name: str) -> str:
        """Get display name, appending ' Lite' for Dashie Lite devices."""
        if self._is_dashie_lite and not base_name.lower().endswith("lite"):
            return f"{base_name} Lite"
        return base_name

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

    @staticmethod
    @config_entries.HANDLERS.register(DOMAIN)
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return DashieOptionsFlow(config_entry)


class DashieOptionsFlow(config_entries.OptionsFlow):
    """Handle Dashie options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Get new values
            new_password = user_input.get(CONF_PASSWORD, "")
            new_port = user_input.get(CONF_PORT, DEFAULT_PORT)
            current_password = self.config_entry.data.get(CONF_PASSWORD, "")
            current_port = self.config_entry.data.get(CONF_PORT, DEFAULT_PORT)

            # If password or port changed, validate by testing connection
            if new_password != current_password or new_port != current_port:
                host = self.config_entry.data.get(CONF_HOST)

                try:
                    async with aiohttp.ClientSession() as session:
                        url = f"http://{host}:{new_port}/?cmd=deviceInfo&type=json"
                        if new_password:
                            url += f"&password={new_password}"

                        async with session.get(
                            url, timeout=aiohttp.ClientTimeout(total=10)
                        ) as response:
                            response.raise_for_status()
                            data = await response.json()

                            # Check for error response
                            if data.get("status") == "ERROR":
                                if "password" in data.get("message", "").lower():
                                    errors["base"] = "invalid_auth"
                                else:
                                    errors["base"] = "cannot_connect"
                except aiohttp.ClientResponseError as err:
                    if err.status == 401:
                        errors["base"] = "invalid_auth"
                    else:
                        errors["base"] = "cannot_connect"
                except Exception:
                    errors["base"] = "cannot_connect"

            if not errors:
                # Update the config entry data with new password/port if changed
                if new_password != current_password or new_port != current_port:
                    new_data = {**self.config_entry.data, CONF_PASSWORD: new_password, CONF_PORT: new_port}
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )

                # Return options (media folder)
                return self.async_create_entry(
                    title="",
                    data={CONF_MEDIA_FOLDER: user_input.get(CONF_MEDIA_FOLDER, DEFAULT_MEDIA_FOLDER)},
                )

        # Get current values
        current_folder = self.config_entry.options.get(
            CONF_MEDIA_FOLDER,
            DEFAULT_MEDIA_FOLDER
        )
        current_password = self.config_entry.data.get(CONF_PASSWORD, "")
        current_port = self.config_entry.data.get(CONF_PORT, DEFAULT_PORT)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_PORT,
                        default=current_port
                    ): int,
                    vol.Optional(
                        CONF_PASSWORD,
                        default=current_password
                    ): str,
                    vol.Optional(
                        CONF_MEDIA_FOLDER,
                        default=current_folder
                    ): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "device_name": self.config_entry.data.get(CONF_DEVICE_NAME, "Dashie"),
            },
        )
