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

from .const import CONF_DEVICE_ID, CONF_HOST, CONF_PASSWORD, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Sensitive fields scrubbed from the entry config dump.
# ⚠️ CONF_DEVICE_ID is deliberately NOT here: HA's redactor replaces a listed value wholesale with
# **REDACTED**, and the device id must be TRUNCATED instead (see _truncate_ids). Listing it here
# would silently turn the truncation back into full redaction.
TO_REDACT = {CONF_PASSWORD, "password"}

# Device ids are truncated, not removed, everywhere in a diagnostics dump (2026-10-08, John: "Agree -
# truncate."). Why: the FULL id is the only thing that addresses the tablet's remote-command channel,
# and these dumps are routinely attached to public GitHub issues. But support matches a pasted dump
# to a device row by its id, so a prefix keeps that match (`device_id LIKE '<prefix>%'`).
# 6 hex characters, uniformly: ids are 32-hex (Widevine hash) or 16-hex (ANDROID_ID), so even the short
# class keeps 10 unknown hex characters (~10^12), and a guess can only be used against a rate-limited
# channel. The trailing ellipsis marks it as a prefix, so nobody searches for it as a whole id.
# Considered and not taken: a short HASH of the id. It leaks nothing, but support would need tooling to
# hash candidate ids to match it; a prefix needs none.
# The id appears in THREE places, and all three are covered: the entry config, the device registry
# identifiers, and the on-device log itself (e.g. every WAKEPROBE line carries `dev=<id>`).
ID_PREFIX_LEN = 6
_MIN_ID_LEN = 12  # never mask short strings that merely happen to match


def _truncate_ids(value: Any, ids: set[str]) -> Any:
    """Return `value` with every occurrence of each full id replaced by its marked prefix."""
    ids = {i for i in ids if isinstance(i, str) and len(i) >= _MIN_ID_LEN}
    if not ids:
        return value

    def mask(text: str) -> str:
        for full in sorted(ids, key=len, reverse=True):  # longest first, so no id masks part of another
            if full in text:
                text = text.replace(full, f"{full[:ID_PREFIX_LEN]}…")
        return text

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            return mask(v)
        if isinstance(v, dict):
            return {walk(k) if isinstance(k, str) else k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        return v

    return walk(value)


def _known_ids(entry: ConfigEntry, device: DeviceEntry | None = None) -> set[str]:
    ids = {entry.data.get(CONF_DEVICE_ID)}
    if device is not None:
        ids |= {ident[1] for ident in device.identifiers if len(ident) > 1 and ident[0] == DOMAIN}
    return {i for i in ids if i}


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
        return [f"(connection error fetching diagnostics: {err})"]
    except Exception as err:  # pragma: no cover - defensive
        _LOGGER.exception("Failed to fetch diagnostics from device")
        return [f"(error fetching diagnostics: {err})"]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Diagnostics for the config entry as a whole."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device_log = await _fetch_device_log(coordinator)
    return _truncate_ids({
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
        "host": entry.data.get(CONF_HOST),
        "device_log": device_log,
    }, _known_ids(entry))


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Diagnostics for a single Dashie device."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device_log = await _fetch_device_log(coordinator)
    return _truncate_ids({
        "device": {
            "name": device.name,
            "model": device.model,
            "manufacturer": device.manufacturer,
            "sw_version": device.sw_version,
            "identifiers": [list(i) for i in device.identifiers],
        },
        "device_log": device_log,
    }, _known_ids(entry, device))
