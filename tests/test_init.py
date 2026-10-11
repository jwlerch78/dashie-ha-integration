"""Setup-lifecycle tests for the Dashie integration.

Reproduces the June 2026 add→"Success"→vanish→rediscover loop: a freshly-added
entry whose *first* coordinator poll fails (slow/temporarily-busy device) was
being auto-removed as an "orphaned ghost" — even though entities simply hadn't
been created yet. The fix only auto-removes orphans during HA startup, never for
an entry added while HA is running.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from homeassistant.core import CoreState, HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from .fake_http_wiring import patch_sessions
from .fake_http import FakeResponse, FakeSession
DOMAIN = "dashie"
DEVICE_ID = "a83e167a70e648255f71a1744d25f740"
IPV4 = "192.168.23.96"
BASE = f"http://{IPV4}:2323"


async def test_fresh_add_survives_failed_first_poll(hass: HomeAssistant) -> None:
    """Add while HA is running + first poll fails → entry must stay loaded.

    With the bug, async_setup_entry removed the entry (→ rediscover loop); the
    fix keeps it (entities come up unavailable until the device answers).
    """
    hass.set_state(CoreState.running)  # a runtime add, not a startup orphan-load

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={"host": IPV4, "port": 2323, "device_id": DEVICE_ID},
    )
    entry.add_to_hass(hass)

    # Device unreachable on the first setup poll(s). Declared per route rather than
    # via a default, so the three the coordinator actually asks for are visible here.
    unreachable = FakeSession({
        "cmd=deviceInfo": asyncio.TimeoutError(),
        "cmd=getRtspStatus": asyncio.TimeoutError(),
        "cmd=getRtspConfig": asyncio.TimeoutError(),
    }, default=asyncio.TimeoutError())
    with patch_sessions(unreachable), patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert unreachable.requests, "the injection was not in effect - no request reached the fake"

    # The entry must NOT have been ghost-removed.
    remaining = {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}
    assert entry.entry_id in remaining, "fresh entry was removed on a failed first poll"


async def test_entities_recover_after_failed_first_poll(hass: HomeAssistant) -> None:
    """After a failed first poll the entry stays with no data; once the device
    answers, the coordinator recovers (unavailable → available)."""
    hass.set_state(CoreState.running)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={"host": IPV4, "port": 2323, "device_id": DEVICE_ID},
    )
    entry.add_to_hass(hass)

    unreachable = FakeSession({"cmd=deviceInfo": asyncio.TimeoutError()},
                              default=asyncio.TimeoutError())
    with patch_sessions(unreachable), patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert unreachable.requests, "the injection was not in effect for the failing poll"
    assert coordinator.last_update_success is False  # entities would be unavailable

    # Device comes back online → next poll succeeds → entities populate. Assigning
    # _session directly is enough here: we hold the instance, and _get_session returns
    # whatever is already there. Prefer that over patching when the object is in hand.
    answering = FakeSession({
        "cmd=deviceInfo": FakeResponse(payload={
            "deviceID": DEVICE_ID, "stableDeviceID": DEVICE_ID, "deviceName": "Echo",
        }),
    }, default=asyncio.TimeoutError())
    coordinator._session = answering
    await coordinator.async_refresh()
    assert any("deviceInfo" in r for r in answering.requests), (
        "the recovered poll never reached the fake"
    )

    assert coordinator.last_update_success is True
    assert coordinator.data["deviceID"] == DEVICE_ID
