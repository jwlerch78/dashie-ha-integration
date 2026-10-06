"""registry_compat: the device lookups that replace calls HA 2026.9 deprecated."""
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dashie.const import DOMAIN
from custom_components.dashie.registry_compat import all_devices, device_by_identifier


async def test_device_by_identifier_finds_the_entrys_device(hass: HomeAssistant) -> None:
    mine, other = MockConfigEntry(domain=DOMAIN), MockConfigEntry(domain=DOMAIN)
    mine.add_to_hass(hass)
    other.add_to_hass(hass)
    reg = dr.async_get(hass)
    dev = reg.async_get_or_create(config_entry_id=mine.entry_id, identifiers={(DOMAIN, "tablet-1")})

    assert device_by_identifier(reg, (DOMAIN, "tablet-1"), mine.entry_id).id == dev.id
    assert device_by_identifier(reg, (DOMAIN, "tablet-1")).id == dev.id
    assert device_by_identifier(reg, (DOMAIN, "nope"), mine.entry_id) is None
    assert device_by_identifier(reg, (DOMAIN, "nope")) is None
    if hasattr(reg, "async_get_device_by_identifier"):  # per-entry lookup exists (HA 2026.9+)
        assert device_by_identifier(reg, (DOMAIN, "tablet-1"), other.entry_id) is None
    assert dev.id in {d.id for d in all_devices(reg)}
