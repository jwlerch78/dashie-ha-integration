"""Diagnostics support for Dashie.

Lets the user download Dashie's on-device diagnostics (in-memory event
buffer + on-disk persistent log) from the HA UI without the tablet
needing internet. Surfaces in HA via:
  Settings → Devices & services → Dashie → ⋮ → Download diagnostics
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import CONF_HOST, CONF_PASSWORD, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Sensitive fields scrubbed from the entry config dump.
TO_REDACT = {CONF_PASSWORD, "password"}


async def _fetch_device_log(coordinator) -> str:
    """Pull the on-device diagnostics log via the local HTTP API."""
    try:
        session = await coordinator._get_session()
        params = {"cmd": "getDiagnosticsLog"}
        if coordinator.password:
            params["password"] = coordinator.password
        url = f"{coordinator.base_url}/"
        async with asyncio.timeout(15):
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                return await response.text()
    except asyncio.TimeoutError:
        return "(timeout fetching diagnostics from device)"
    except aiohttp.ClientError as err:
        return f"(connection error fetching diagnostics: {err})"
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.exception("Failed to fetch diagnostics from device")
        return f"(error fetching diagnostics: {err})"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Diagnostics for the config entry as a whole."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device_log = await _fetch_device_log(coordinator)
    return {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
        "host": entry.data.get(CONF_HOST),
        "device_log": device_log,
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Diagnostics for a single Dashie device."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device_log = await _fetch_device_log(coordinator)
    return {
        "device": {
            "name": device.name,
            "model": device.model,
            "manufacturer": device.manufacturer,
            "sw_version": device.sw_version,
            "identifiers": [list(i) for i in device.identifiers],
        },
        "device_log": device_log,
    }
