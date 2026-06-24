"""Config flow for Dashie integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PASSWORD
from homeassistant.data_entry_flow import AbortFlow, FlowResult

from .const import (
    DOMAIN,
    DEFAULT_PORT,
    DEFAULT_MEDIA_FOLDER,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_MEDIA_FOLDER,
    host_for_url as _host_for_url,
)

_LOGGER = logging.getLogger(__name__)


def _normalize_host(raw_host: str) -> tuple[str, int | None]:
    """Normalize a user-entered host into a bare host + optional embedded port.

    Tolerates copy/paste mistakes that otherwise produce a broken URL and a
    misleading "cannot connect": an ``http://``/``https://`` scheme, a trailing
    slash or path, surrounding whitespace, and an embedded ``:port``. The bare
    host is what gets stored and reused by the coordinator at runtime, so a
    clean value here fixes both setup and every later poll.

    Returns ``(host, port)`` where ``port`` is ``None`` if none was embedded.
    IPv6 (multiple colons / bracketed) is left untouched.
    """
    host = (raw_host or "").strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    for sep in ("/", "?", "#"):
        host = host.split(sep, 1)[0]
    host = host.strip().strip(".")

    port: int | None = None
    # Peel a single embedded port (IPv4 / hostname only; skip IPv6).
    if host.count(":") == 1 and not host.startswith("["):
        candidate_host, _, candidate_port = host.partition(":")
        if candidate_port.isdigit():
            host, port = candidate_host, int(candidate_port)

    return host, port


def _select_discovery_host(discovery_info) -> str | None:
    """Pick the best address from a zeroconf discovery, strongly preferring IPv4.

    Dashie devices advertise an IPv4 plus IPv6 (ULA + ``fe80::`` link-local).
    ``discovery_info.host`` can surface an IPv6 first; a link-local needs a zone
    HA core can't use, and any bare IPv6 breaks the unbracketed URL — both of
    which make the discovery flow silently abort ("didn't auto-detect"). Prefer
    the first IPv4, then a non-link-local IPv6, and only fall back to ``.host``.
    """
    candidates: list[str] = [
        str(ip) for ip in (getattr(discovery_info, "ip_addresses", None) or [])
    ]
    if not candidates and discovery_info.host:
        candidates.append(discovery_info.host)

    for h in candidates:  # IPv4 first
        if "." in h and ":" not in h:
            return h
    for h in candidates:  # then any non-link-local IPv6
        if not h.lower().startswith("fe80:") and "%" not in h:
            return h
    return candidates[0] if candidates else discovery_info.host


class DashieConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dashie."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._password: str = ""
        self._device_info: dict | None = None
        self._is_dashie: bool = False

    async def async_step_zeroconf(self, discovery_info: Any) -> FlowResult:
        """Handle zeroconf/mDNS discovery."""
        _LOGGER.debug("🔍 Zeroconf discovery received!")
        _LOGGER.debug("  Name: %s", discovery_info.name)
        _LOGGER.debug("  Type: %s", discovery_info.type)
        _LOGGER.debug("  Host: %s", discovery_info.host)
        _LOGGER.debug("  Port: %s", discovery_info.port)
        _LOGGER.debug("  Properties: %s", discovery_info.properties)

        # Extract host and port from zeroconf discovery — prefer IPv4 over the
        # advertised IPv6/link-local addresses (a bare IPv6 silently breaks setup).
        self._host, _ = _normalize_host(_select_discovery_host(discovery_info) or "")
        self._port = discovery_info.port or DEFAULT_PORT

        if not self._host:
            return self.async_abort(reason="no_host")

        # Get device metadata from TXT records
        properties = discovery_info.properties or {}
        device_name = properties.get("name", discovery_info.name or "Dashie")
        device_uuid = properties.get("uuid")

        # Check if we already have an entry with this host IP
        for entry in self._async_current_entries():
            if entry.data.get(CONF_HOST) == self._host:
                _LOGGER.debug("❌ Aborting: Device at %s already configured (entry: %s)", self._host, entry.entry_id)
                return self.async_abort(reason="already_configured")

        _LOGGER.debug("✅ No existing entry for %s, continuing discovery", self._host)

        # Set unique_id from UUID if available
        if device_uuid:
            _LOGGER.debug("🔑 Setting unique_id from Zeroconf UUID: %s", device_uuid)
            await self.async_set_unique_id(device_uuid)
            _LOGGER.debug("🔍 Checking if unique_id already configured...")
            self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
            _LOGGER.debug("✅ Unique_id not already configured, continuing")

        # Try to fetch device info without password first
        try:
            _LOGGER.debug("🌐 Fetching device info from %s:%s", self._host, self._port)
            self._device_info = await self._fetch_device_info()
            # Prefer the hardware-backed stable ID (Widevine MediaDrm); fall back
            # to legacy deviceID for old APKs that don't expose stableDeviceID yet.
            device_id = (
                self._device_info.get("stableDeviceID")
                or self._device_info.get("deviceID")
            )
            _LOGGER.debug("📱 Received deviceID: %s", device_id)

            if device_id:
                # Update unique_id with actual device_id (more reliable than UUID)
                _LOGGER.debug("🔑 Updating unique_id to deviceID: %s", device_id)
                await self.async_set_unique_id(device_id)
                _LOGGER.debug("🔍 Checking if deviceID already configured...")
                self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
                _LOGGER.debug("✅ DeviceID not already configured, proceeding to confirm")

                self._device_info["deviceName"] = device_name
                self._is_dashie = True  # Zeroconf discovery is Dashie only
                self.context["title_placeholders"] = {"name": device_name}
                return await self.async_step_confirm()

        except AbortFlow:
            # already_configured / already_in_progress must propagate.
            raise
        except aiohttp.ClientResponseError as err:
            _LOGGER.debug("❌ API returned error %s - going to password step", err.status)
            if err.status == 401:
                # Password required - go to password step
                self.context["title_placeholders"] = {"name": device_name}
                return await self.async_step_password()
            raise
        except Exception as err:
            _LOGGER.error("❌ Failed to fetch device info: %s", err)
            return self.async_abort(reason="cannot_connect")

        _LOGGER.debug("❌ Aborting: No deviceID in response")
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
                device_id = (
                    self._device_info.get("stableDeviceID")
                    or self._device_info.get("deviceID")
                )

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
            except AbortFlow:
                # already_configured / already_in_progress must propagate, not be
                # relabeled "cannot_connect" by the broad handler below.
                raise
            except aiohttp.ClientResponseError as err:
                if err.status == 401:
                    errors["base"] = "invalid_auth"
                else:
                    _LOGGER.error("Password step HTTP error (host=%s port=%s): %s", self._host, self._port, err)
                    errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Password step failed (host=%s port=%s): %s", self._host, self._port, err)
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
                    CONF_DEVICE_ID: (
                        self._device_info.get("stableDeviceID")
                        or self._device_info.get("deviceID")
                    ),
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
            self._host, embedded_port = _normalize_host(user_input[CONF_HOST])
            # An embedded :port (e.g. "192.168.1.5:8080") wins over the field default.
            self._port = embedded_port or user_input.get(CONF_PORT, DEFAULT_PORT)
            self._password = user_input.get(CONF_PASSWORD, "")

            try:
                self._device_info = await self._fetch_device_info()
                device_id = (
                    self._device_info.get("stableDeviceID")
                    or self._device_info.get("deviceID")
                )

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
            except AbortFlow:
                # Flow-control signals (already_configured / already_in_progress)
                # must propagate — they are NOT connection failures. Without this,
                # the broad `except Exception` below relabels them "cannot_connect".
                raise
            except aiohttp.ClientResponseError as err:
                if err.status == 401:
                    errors["base"] = "invalid_auth"
                else:
                    _LOGGER.error("Manual add HTTP error (host=%s port=%s): %s", self._host, self._port, err)
                    errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Manual add failed (host=%s port=%s): %s", self._host, self._port, err)
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
        """Get display name for the device."""
        # Strip legacy " Lite" suffix if present
        if base_name.endswith(" Lite"):
            base_name = base_name[:-5]
        return base_name

    async def _fetch_device_info(self) -> dict:
        """Fetch device info from the device."""
        async with aiohttp.ClientSession() as session:
            url = f"http://{_host_for_url(self._host)}:{self._port}/?cmd=deviceInfo&type=json"
            if self._password:
                url += f"&password={self._password}"

            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5, connect=3)) as response:
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
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return DashieOptionsFlow()


class DashieOptionsFlow(config_entries.OptionsFlow):
    """Handle Dashie options."""

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
                        url = f"http://{_host_for_url(host)}:{new_port}/?cmd=deviceInfo&type=json"
                        if new_password:
                            url += f"&password={new_password}"

                        async with session.get(
                            url, timeout=aiohttp.ClientTimeout(total=5, connect=3)
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
                    # Reload the entry to reinitialize coordinator with new port/password
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)

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
