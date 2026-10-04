"""What a tablet should listen for when it cannot listen to everything.

With the screen off, Android stops unfiltered Bluetooth scans entirely; filtered scans
keep running at full rate (measured on a Fire HD, Android 11). So the tablet needs a
list of filters, and this HA is the one that knows what it cares about:

* ``matchers`` — the Bluetooth matchers of every integration set up on this HA, as HA
  declares them (``local_name``, ``manufacturer_id``, ``service_uuid``, ...). Sent raw;
  the tablet decides which ones it can turn into Android scan filters (a wildcard
  ``local_name`` cannot be one).
* ``addresses`` — the Bluetooth address of every device HA already has in its device
  registry. An exact address is the one filter that always works, wildcard name or not.
* ``names`` — HA's name for each of those addresses, so the tablet can say which of
  HA's devices it is hearing in HA's words ("Pool thermometer"), not as addresses.

The shape is a contract with the Android app (``BleLearnedFilters.kt``): bump
``CONFIG_VERSION`` when it changes.
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_bluetooth

from .registry_compat import all_devices

CONFIG_VERSION = 1

# Matcher keys the tablet understands. Anything else HA adds later is dropped here
# rather than sent to an app that would not know what to do with it.
_MATCHER_KEYS = (
    "local_name",
    "manufacturer_id",
    "manufacturer_data_start",
    "service_uuid",
    "service_data_uuid",
)


async def async_build_scan_config(hass: HomeAssistant) -> dict[str, Any]:
    """Return the filters a tablet should keep while its screen is off."""
    configured = set(hass.config_entries.async_domains())
    matchers: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for matcher in await async_get_bluetooth(hass):
        if matcher.get("domain") not in configured:
            continue
        slim = {k: matcher[k] for k in _MATCHER_KEYS if k in matcher}
        if not slim:
            continue
        key = tuple(sorted((k, str(v)) for k, v in slim.items()))
        if key in seen:
            continue
        seen.add(key)
        matchers.append({"domain": matcher["domain"], **slim})

    # HA's own Bluetooth adapters are in the registry with a Bluetooth address too. They are receivers, not
    # devices: not something the tablet hears for HA, nor worth one of its screen-off filters.
    adapters = {e.entry_id for e in hass.config_entries.async_entries("bluetooth")}
    names: dict[str, str] = {}
    for device in all_devices(dr.async_get(hass)):
        if device.config_entries & adapters:
            continue
        for kind, value in device.connections:
            if kind == dr.CONNECTION_BLUETOOTH:
                names[value.upper()] = device.name_by_user or device.name or ""
    # ``names`` was added without a version bump: an older app ignores keys it does not know.
    return {"v": CONFIG_VERSION, "matchers": matchers, "addresses": sorted(names), "names": names}
