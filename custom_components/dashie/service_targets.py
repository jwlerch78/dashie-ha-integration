"""Resolve a service call's `device_id` to the Dashie devices it names.

The services UI fills `device_id` with an entity picker, so the value is normally an
entity id or a list of them; YAML automations may pass a Home Assistant device id
instead. Both resolve to the coordinators of the config entries they belong to.

An omitted `device_id` means every Dashie device, which is what these services did
before targeting was honored, so automations that leave it out keep working. A value
that matches no Dashie device raises instead of silently doing nothing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import DashieCoordinator


def all_coordinators(hass: HomeAssistant) -> list[DashieCoordinator]:
    """Every loaded Dashie coordinator."""
    from .coordinator import DashieCoordinator

    return [
        v for v in hass.data.get(DOMAIN, {}).values()
        if isinstance(v, DashieCoordinator)
    ]


def resolve_target_coordinators(hass: HomeAssistant, device_id) -> list[DashieCoordinator]:
    """Coordinators for the entity ids / device ids in `device_id`, de-duplicated."""
    if device_id is None or device_id == "" or device_id == []:
        return all_coordinators(hass)

    items = [device_id] if isinstance(device_id, str) else list(device_id)
    by_entry = {c.config_entry.entry_id: c for c in all_coordinators(hass) if c.config_entry}
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)

    targets: list[DashieCoordinator] = []
    for item in items:
        entry_ids: set[str] = set()
        entity = ent_reg.async_get(str(item))
        if entity and entity.config_entry_id:
            entry_ids.add(entity.config_entry_id)
        else:
            device = dev_reg.async_get(str(item))
            if device:
                entry_ids.update(device.config_entries)
        matched = [by_entry[e] for e in entry_ids if e in by_entry]
        if not matched:
            raise ServiceValidationError(
                f"{item} is not a Dashie device or one of its entities"
            )
        for coordinator in matched:
            if coordinator not in targets:
                targets.append(coordinator)
    return targets
