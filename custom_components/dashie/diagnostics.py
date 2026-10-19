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


def _safe_err(err: BaseException) -> str:
    """Describe an exception WITHOUT its own message, because the message carries the URL.

    🔴 THIS IS A CREDENTIAL BOUNDARY, NOT TIDYING. The device API password is a QUERY
    PARAMETER (`?cmd=getDiagnosticsLog&password=...`), and `raise_for_status()` below
    makes `aiohttp.ClientResponseError` reachable, whose `str()` embeds the full request
    URL. `_fetch_device_log`'s return value is rendered into the file a user downloads
    from Settings -> Devices & services -> Dashie -> Download diagnostics, i.e. the file
    we ASK reporters to attach to GitHub issues. Measured 2026-10-18 against a real
    aiohttp server returning 500:

        (connection error fetching diagnostics: 500, message='Internal Server Error',
         url='http://host:2323/?cmd=getDiagnosticsLog&password=MyD3vicePassw0rd!')

    ⚠️ The trigger is correlated with the reason someone generates the file. The device
    answers 401 exactly when the stored password is wrong or stale, 401 is >= 400 so it
    raises, and a household debugging "why is Dashie unavailable" is therefore the most
    likely one to publish its own tablet password.

    ⚠️ `TO_REDACT` does NOT cover this. It scrubs `entry.data`; this path leaves through
    an exception string and never touches that dict. A redaction list is not a boundary.

    ⇒ fail-safe by CONSTRUCTION, not by pattern-matching: this never interpolates
    `str(err)` at all, so no future exception type can leak through it. That is the
    opposite choice from a redactor, which must recognise a URL to scrub it and leaks
    whenever it fails to.

    Nothing diagnostic is lost. Type plus HTTP status separates the cases anyone acts on
    (401 wrong password / 404 wrong path / 5xx device fault / ClientConnectorError not
    reachable), and the host is already reported beside this value.
    """
    status = getattr(err, "status", None)
    if status is not None:
        return f"{type(err).__name__} HTTP {status}"
    return type(err).__name__


async def _fetch_device_log(coordinator) -> list[str]:
    """Pull the on-device diagnostics log via the local HTTP API.

    Returns the log as a list of lines so the JSON serializer emits a real
    line-per-entry array rather than a single 2 MB string with escaped \\n —
    most editors silently truncate long lines and hide the rest.
    """
    try:
        session = await coordinator._get_session()
        params = {"cmd": "getDiagnosticsLog"}
        if coordinator.password:
            params["password"] = coordinator.password
        url = f"{coordinator.base_url}/"
        async with asyncio.timeout(15):
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                text = await response.text()
                return text.splitlines()
    except asyncio.TimeoutError:
        return ["(timeout fetching diagnostics from device)"]
    except aiohttp.ClientError as err:
        return [f"(connection error fetching diagnostics: {_safe_err(err)})"]
    except Exception as err:  # pragma: no cover - defensive
        # Was _LOGGER.exception, which writes the traceback - and therefore the exception
        # MESSAGE, and therefore the URL - to home-assistant.log at ERROR, which HA shows
        # by default. Same credential, second channel. The traceback is worth little for a
        # single HTTP GET in a ten-line block; a tablet password in a default-level log is
        # what v1.6.3 already had to fix once.
        _LOGGER.error("Failed to fetch diagnostics from device: %s", _safe_err(err))
        return [f"(error fetching diagnostics: {_safe_err(err)})"]


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
