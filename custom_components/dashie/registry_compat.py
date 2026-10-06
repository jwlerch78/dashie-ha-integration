"""Device registry access that works on every HA this integration supports.

HA 2026.9 deprecated using ``device_registry.devices`` as a mapping (``.values()``,
``[id]``): it logs for custom integrations now and breaks in 2027.9. Iterating it is
the supported way, and yields ``DeviceEntry`` values there; on older HA (2025.1) the
same iteration yields device ids. ``all_devices`` hides the difference.

HA 2026.9 also deprecated ``async_get_device(identifiers=...)`` (identifiers are no longer
unique across config entries); ``device_by_identifier`` uses the per-entry lookup that
replaced it where it exists.
"""
from __future__ import annotations

from collections.abc import Iterator

from homeassistant.helpers import device_registry as dr


def all_devices(registry: dr.DeviceRegistry) -> Iterator[dr.DeviceEntry]:
    """Every device in the registry, as entries, on old and new HA alike."""
    devices = registry.devices
    for item in list(devices):
        yield item if isinstance(item, dr.DeviceEntry) else devices[item]


def device_by_identifier(
    registry: dr.DeviceRegistry, identifier: tuple[str, str], config_entry_id: str | None = None
) -> dr.DeviceEntry | None:
    """The device with ``identifier``: within ``config_entry_id`` when given, else the first anywhere."""
    if config_entry_id is not None and hasattr(registry, "async_get_device_by_identifier"):
        return registry.async_get_device_by_identifier(identifier, config_entry_id)
    if hasattr(registry, "async_get_device_by_identifier"):
        return next((d for d in all_devices(registry) if identifier in d.identifiers), None)
    return registry.async_get_device(identifiers={identifier})
