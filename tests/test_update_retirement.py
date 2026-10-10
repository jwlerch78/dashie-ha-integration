"""Retiring the integration's own update entity must DELETE its registry row.

Home Assistant keeps the registry row for an entity whose platform has stopped
providing it and renders it `unavailable` rather than removing it. So dropping
`Platform.UPDATE` on its own would leave every existing household a dead
"Dashie Integration Update - unavailable" row - the same class of confusing report the
retirement exists to end. `_async_remove_retired_update_entity` removes it instead.

🔴 **ABSENT, NOT UNAVAILABLE, IS THE WHOLE TEST.** `unavailable` IS the failure mode, and
a row that is merely unavailable still reads as an entity to the user, so every leg below
asserts `async_get_entity_id(...) is None` rather than inspecting a state.

⚠️ **WHAT THESE LEGS DO NOT PROVE, stated because green legs would otherwise imply it:**
they prove the cleanup removes the row. They do NOT prove Home Assistant leaves the row
behind in the first place - that premise is the entire reason for the change, and it is
only observable on a real box that already has the entity, after an update + restart.
That is the acceptance test, and it needs a household with two or more tablets configured
on ONE HA instance (the ownership flag was per-instance, so two tablets on two instances
would not exercise it).

🧭 **HARNESS FACT, measured here 2026-10-09, which decided how leg 4 is written:**
`hass.config_entries.async_setup(<one entry_id>)` sets up the *component*, and the
component sets up **every** config entry of the domain - both entries come back LOADED.
So there is no way through this path to observe "what one entry's pass did and the other's
did not": every entry's cleanup has already run by the time the await returns. An earlier
version of leg 4 asserted that setting up the non-owning entry left the row alone and went
red for exactly that reason - the row was gone because the OWNER's own pass had removed it,
which is the correct behavior. The entry-scoping claim is therefore pinned by calling the
callback directly (leg 4a); leg 4b keeps the end-to-end multi-tablet assertion, which is
still discriminating because a "run once on the first entry" guard would leave the second
tablet's row behind.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from aioresponses import aioresponses
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dashie import (
    RETIRED_UPDATE_DOMAIN,
    RETIRED_UPDATE_UNIQUE_ID,
    _async_remove_retired_update_entity,
)

DOMAIN = "dashie"
# Deliberately the LITERAL, not the imported constant: this string is a wire value baked
# into the registry of every household that ever loaded the old platform. A test that
# re-used the constant would follow a rename and stop protecting those installs.
RETIRED_UID = "dashie_integration_update"
IPV4 = "192.168.23.96"
BASE = f"http://{IPV4}:2323"


def _entry(hass: HomeAssistant, device_id: str, title: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=device_id, title=title,
        data={"host": IPV4, "port": 2323, "device_id": device_id},
    )
    entry.add_to_hass(hass)
    return entry


def _register(hass: HomeAssistant, entry: MockConfigEntry, domain: str, uid: str) -> str:
    """Put a row in the entity registry owned by `entry`, as the old platform did."""
    return er.async_get(hass).async_get_or_create(
        domain, DOMAIN, uid, config_entry=entry,
    ).entity_id


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, device_id: str) -> None:
    """Run setup with the device answering, platforms stubbed out.

    NB: this loads the component, so EVERY config entry of the domain is set up, not
    just `entry` - see the harness note in the module docstring.
    """
    hass.set_state(CoreState.running)
    with aioresponses() as mock, patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        mock.get(
            f"{BASE}/?cmd=deviceInfo&type=json",
            payload={"deviceID": device_id, "stableDeviceID": device_id, "deviceName": "Kitchen"},
            repeat=True,
        )
        mock.get(f"{BASE}/?cmd=getRtspStatus", exception=asyncio.TimeoutError(), repeat=True)
        mock.get(f"{BASE}/?cmd=getRtspConfig", exception=asyncio.TimeoutError(), repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def test_the_retired_unique_id_is_the_shipped_wire_value() -> None:
    """The constant must keep matching what shipped, or the cleanup misses real rows.

    Existing households hold `dashie_integration_update` in their registry. Renaming the
    constant without a migration would silently stop finding them, and nothing else in
    the suite would notice.
    """
    assert RETIRED_UPDATE_UNIQUE_ID == RETIRED_UID
    assert RETIRED_UPDATE_DOMAIN == "update"


async def test_the_retired_row_is_removed_not_left_unavailable(hass: HomeAssistant) -> None:
    """Leg 1 - the repair, through the real setup path."""
    entry = _entry(hass, "dev1", "Kitchen")
    _register(hass, entry, "update", RETIRED_UID)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("update", DOMAIN, RETIRED_UID) is not None, (
        "the row must exist before setup, or this leg is vacuous"
    )

    await _setup(hass, entry, "dev1")

    assert registry.async_get_entity_id("update", DOMAIN, RETIRED_UID) is None, (
        "the retired update entity is still registered; an unavailable row is the failure "
        "mode, so absence is the requirement"
    )


async def test_it_removes_only_that_row(hass: HomeAssistant) -> None:
    """Leg 2 - POSITIVE CONTROL, and what makes leg 1 mean anything.

    Without it, leg 1 passes just as well against a cleanup that empties the entry's
    registry entirely - which would delete every entity on the tablet.
    """
    entry = _entry(hass, "dev1", "Kitchen")
    _register(hass, entry, "update", RETIRED_UID)
    sibling = _register(hass, entry, "sensor", "dev1_battery")

    await _setup(hass, entry, "dev1")

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("update", DOMAIN, RETIRED_UID) is None
    assert registry.async_get_entity_id("sensor", DOMAIN, "dev1_battery") == sibling, (
        "the cleanup removed an unrelated entity; it must match the retired unique_id exactly"
    )


async def test_it_matches_the_domain_too_not_the_unique_id_alone(hass: HomeAssistant) -> None:
    """The registry key is (platform, domain, unique_id) - match on every part we know.

    Raised by X in review of 6cf376a: the production match tested `unique_id` alone while
    this file looks the row up as ("update", DOMAIN, unique_id), so the code was WIDER than
    the test asserting it. Nothing holds this unique_id in another domain today - every
    other Dashie entity is device-prefixed - so this leg is what makes the narrowing
    stick rather than evidence of a live bug. A test stricter than its code cannot notice
    the code widening in that dimension.
    """
    entry = _entry(hass, "dev1", "Kitchen")
    _register(hass, entry, "update", RETIRED_UID)
    # Same unique_id, DIFFERENT domain. Legitimate: the registry allows it.
    impostor = _register(hass, entry, "sensor", RETIRED_UID)

    await _setup(hass, entry, "dev1")

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("update", DOMAIN, RETIRED_UID) is None
    assert registry.async_get_entity_id("sensor", DOMAIN, RETIRED_UID) == impostor, (
        "an entity sharing the unique_id in another domain was removed; the match must "
        "be on (domain, unique_id), not unique_id alone"
    )


async def test_it_is_idempotent_without_a_guard_flag(hass: HomeAssistant) -> None:
    """Leg 3 - run it twice. Pins "no hass.data guard" BEHAVIOURALLY.

    A grep for the absence of a guard proves nothing about idempotence; running the
    cleanup a second time does. The second pass must find nothing and must not raise -
    on the declared floor (2025.1) `async_remove` does a bare `self.entities.pop(
    entity_id)` and so raises KeyError for an absent entity, where 2026.5+ guards.

    🔴 **The LOADED assertion is the load-bearing half, not the `is None`.** Checked by
    fault injection (2026-10-09): a mutant that remembers removed entity_ids across passes
    and re-removes them - precisely the hazard named above - leaves this leg GREEN if it
    only asserts absence, because the row was already gone from the first pass and the
    KeyError merely fails the second setup. Absence survives the bug; a loaded entry
    does not.
    """
    entry = _entry(hass, "dev1", "Kitchen")
    _register(hass, entry, "update", RETIRED_UID)

    await _setup(hass, entry, "dev1")
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("update", DOMAIN, RETIRED_UID) is None

    # Second pass over an entry that no longer has the row.
    await hass.config_entries.async_unload(entry.entry_id)
    await _setup(hass, entry, "dev1")
    assert registry.async_get_entity_id("update", DOMAIN, RETIRED_UID) is None
    assert entry.state is ConfigEntryState.LOADED, (
        f"the second pass broke setup ({entry.state}); the cleanup must tolerate finding "
        "nothing - it is unguarded precisely because it is idempotent"
    )


async def test_the_lookup_is_scoped_to_its_own_entry(hass: HomeAssistant) -> None:
    """Leg 4a - entry-scoping, pinned by calling the callback directly.

    `er.async_entries_for_config_entry` is entry-scoped, which is WHY the cleanup has to
    run on every entry rather than once. Driven directly because the setup path sets up
    every entry at once and so cannot show one entry's pass in isolation (module docstring).
    """
    first = _entry(hass, "dev1", "Kitchen")
    second = _entry(hass, "dev2", "Playroom")
    _register(hass, second, "update", RETIRED_UID)   # owned by the SECOND entry
    registry = er.async_get(hass)

    _async_remove_retired_update_entity(hass, first)
    assert registry.async_get_entity_id("update", DOMAIN, RETIRED_UID) is not None, (
        "the non-owning entry's pass removed another entry's row; the lookup must stay "
        "scoped to the entry it was given"
    )

    _async_remove_retired_update_entity(hass, second)
    assert registry.async_get_entity_id("update", DOMAIN, RETIRED_UID) is None, (
        "the owning entry's pass did not remove the row"
    )


async def test_a_second_tablets_row_goes_when_every_entry_runs(hass: HomeAssistant) -> None:
    """Leg 4b - the multi-tablet case end to end, and the unit mirror of the acceptance control.

    The row belongs to whichever entry originally created it - in a real household, not
    necessarily the one HA happens to load first. Still discriminating despite the
    all-entries-at-once harness fact: a "run it once on the first entry" guard would leave
    this row behind, because the row's owner is the SECOND entry.
    """
    first = _entry(hass, "dev1", "Kitchen")
    second = _entry(hass, "dev2", "Playroom")
    _register(hass, second, "update", RETIRED_UID)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("update", DOMAIN, RETIRED_UID) is not None

    await _setup(hass, first, "dev1")

    assert registry.async_get_entity_id("update", DOMAIN, RETIRED_UID) is None, (
        "a second tablet's retired row survived; the cleanup must run on every entry"
    )
    assert {first.entry_id, second.entry_id} <= {
        e.entry_id for e in hass.config_entries.async_entries(DOMAIN)
    }, "an entry was removed; the cleanup must touch entities only"


async def test_it_touches_no_device_and_no_config_entry(hass: HomeAssistant) -> None:
    """Leg 5 - the hazard with a live precedent in this file.

    Removing a config entry that had no registered entities caused an
    add -> "Success" -> vanish -> rediscover loop (see the orphan-removal comment in
    __init__.py). The cleanup must call async_remove(entity_id) and nothing else.
    """
    entry = _entry(hass, "dev1", "Kitchen")
    _register(hass, entry, "update", RETIRED_UID)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "dev1")}, name="Kitchen",
    )

    await _setup(hass, entry, "dev1")

    assert entry.entry_id in {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}, (
        "the config entry was removed - this is the add/vanish/rediscover hazard"
    )
    assert dr.async_get(hass).async_get(device.id) is not None, "the device registry row was removed"
