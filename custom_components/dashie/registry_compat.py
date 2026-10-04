"""Device registry access that works on every HA this integration supports.

HA 2026.9 deprecated using ``device_registry.devices`` as a mapping (``.values()``,
``[id]``): it logs for custom integrations now and breaks in 2027.9. Iterating it is
the supported way, and yields ``DeviceEntry`` values there; on older HA (2025.1) the
same iteration yields device ids. ``all_devices`` hides the difference.
"""
from __future__ import annotations

from collections.abc import Iterator

from homeassistant.helpers import device_registry as dr


def all_devices(registry: dr.DeviceRegistry) -> Iterator[dr.DeviceEntry]:
    """Every device in the registry, as entries, on old and new HA alike."""
    devices = registry.devices
    for item in list(devices):
        yield item if isinstance(item, dr.DeviceEntry) else devices[item]
