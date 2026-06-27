"""Setup-lifecycle tests for the Dashie integration.

Reproduces the June 2026 add→"Success"→vanish→rediscover loop: a freshly-added
entry whose *first* coordinator poll fails (slow/temporarily-busy device) was
being auto-removed as an "orphaned ghost" — even though entities simply hadn't
been created yet. The fix only auto-removes orphans during HA startup, never for
an entry added while HA is running.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from aioresponses import aioresponses
from homeassistant.core import CoreState, HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

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

    with aioresponses() as mock, patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        # Device unreachable on the first setup poll(s).
        mock.get(f"{BASE}/?cmd=deviceInfo&type=json", exception=asyncio.TimeoutError(), repeat=True)
        mock.get(f"{BASE}/?cmd=getRtspStatus", exception=asyncio.TimeoutError(), repeat=True)
        mock.get(f"{BASE}/?cmd=getRtspConfig", exception=asyncio.TimeoutError(), repeat=True)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # The entry must NOT have been ghost-removed.
    remaining = {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}
    assert entry.entry_id in remaining, "fresh entry was removed on a failed first poll"
