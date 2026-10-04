"""Bluetooth for HA: a tablet's adverts reach HA's Bluetooth manager through the webhook.

The end-to-end test is the integration half of the plan's "done": an advert posted to
the entry's webhook comes out of HA's Bluetooth API with the tablet's scanner as its
source. Every absence asserted here is paired with the same lookup succeeding.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from aioresponses import aioresponses
from homeassistant.components import bluetooth
from homeassistant.core import CoreState, HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dashie.ble_scanner import _parse_advert, scanner_source
from custom_components.dashie.ble_webhook import CMD_SET_WEBHOOK, CONF_BLE_WEBHOOK_ID, webhook_hash

DOMAIN = "dashie"
DEVICE_ID = "a83e167a70e648255f71a1744d25f740"
IPV4 = "192.168.23.96"
BASE = f"http://{IPV4}:2323"
INKBIRD = "49:24:03:27:01:C7"
ADVERT = {"a": INKBIRD, "r": -61, "n": "tps", "md": {"10241": "0a0b0c"}, "age": 250}


async def _setup_entry(hass: HomeAssistant, device_info: dict | None = None) -> MockConfigEntry:
    hass.set_state(CoreState.running)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DEVICE_ID, title="Kitchen",
        data={"host": IPV4, "port": 2323, "device_id": DEVICE_ID},
    )
    entry.add_to_hass(hass)
    with aioresponses() as mock, patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        if device_info is None:
            mock.get(f"{BASE}/?cmd=deviceInfo&type=json", exception=asyncio.TimeoutError(), repeat=True)
        else:
            mock.get(f"{BASE}/?cmd=deviceInfo&type=json", payload=device_info, repeat=True)
        mock.get(f"{BASE}/?cmd=getRtspStatus", exception=asyncio.TimeoutError(), repeat=True)
        mock.get(f"{BASE}/?cmd=getRtspConfig", exception=asyncio.TimeoutError(), repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def test_scanner_source_is_stable_and_locally_administered() -> None:
    a, b = scanner_source(DEVICE_ID), scanner_source(DEVICE_ID)
    assert a == b and len(a) == 17
    first = int(a[:2], 16)
    assert first & 0x02 and not first & 0x01  # locally administered, unicast
    assert scanner_source("other") != a


def test_parse_advert() -> None:
    parsed = _parse_advert(ADVERT)
    assert parsed is not None
    address, rssi, name, uuids, sd, md, tx, age = parsed
    assert (address, rssi, name, md, tx, age) == (INKBIRD, -61, "tps", {10241: b"\x0a\x0b\x0c"}, None, 0.25)
    assert _parse_advert({**ADVERT, "md": {"x": "zz"}}) is None
    assert _parse_advert({"r": -50}) is None
    assert _parse_advert({**ADVERT, "a": "short"}) is None


async def test_webhook_feeds_hass_bluetooth(hass: HomeAssistant, enable_bluetooth, hass_client_no_auth) -> None:
    entry = await _setup_entry(hass)
    webhook_id = entry.data[CONF_BLE_WEBHOOK_ID]
    client = await hass_client_no_auth()
    url = f"/api/webhook/{webhook_id}"

    assert bluetooth.async_last_service_info(hass, INKBIRD, connectable=False) is None

    resp = await client.post(url, json={"v": 1, "enabled": True, "adverts": [ADVERT, {"bad": 1}]})
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "ok" and body["accepted"] == 1
    assert "config" in body and body["config"]["v"] == 1

    info = bluetooth.async_last_service_info(hass, INKBIRD, connectable=False)
    assert info is not None
    assert info.source == scanner_source(DEVICE_ID)
    assert info.name == "tps" and info.manufacturer_data == {10241: b"\x0a\x0b\x0c"}

    # Same config hash → the config is not resent.
    resp = await client.post(url, json={"adverts": [ADVERT], "configHash": body["configHash"]})
    assert "config" not in await resp.json()

    # Turning it off unregisters the scanner.
    assert bluetooth.async_scanner_by_source(hass, scanner_source(DEVICE_ID)) is not None
    resp = await client.post(url, json={"enabled": False})
    assert (await resp.json())["status"] == "ok"
    assert bluetooth.async_scanner_by_source(hass, scanner_source(DEVICE_ID)) is None


async def test_webhook_without_bluetooth_does_not_break_setup(hass: HomeAssistant, hass_client_no_auth) -> None:
    entry = await _setup_entry(hass)
    assert entry.entry_id in hass.data[DOMAIN]
    client = await hass_client_no_auth()
    resp = await client.post(f"/api/webhook/{entry.data[CONF_BLE_WEBHOOK_ID]}", json={"adverts": [ADVERT]})
    assert (await resp.json())["status"] == "no_bluetooth"


@pytest.mark.parametrize(
    ("ha_ble", "expect_handoff"),
    [({"supported": True, "webhookHash": ""}, True), (None, False), ({"supported": False}, False)],
)
async def test_handoff_only_to_tablets_that_support_it(hass: HomeAssistant, ha_ble, expect_handoff) -> None:
    info = {"deviceID": DEVICE_ID, "deviceName": "Kitchen"}
    if ha_ble is not None:
        info["haBle"] = ha_ble
    with patch(
        "custom_components.dashie.coordinator.DashieCoordinator.async_send",
        AsyncMock(return_value=type("R", (), {"ok": True})()),
    ) as send:
        entry = await _setup_entry(hass, info)
        coordinator = hass.data[DOMAIN][entry.entry_id]
        coordinator.async_update_listeners()
        await hass.async_block_till_done()
    calls = [c for c in send.call_args_list if c.args and c.args[0] == CMD_SET_WEBHOOK]
    assert bool(calls) == expect_handoff
    if expect_handoff:
        assert calls[0].kwargs["webhookId"] == entry.data[CONF_BLE_WEBHOOK_ID]
        assert calls[0].kwargs["path"] == f"/api/webhook/{entry.data[CONF_BLE_WEBHOOK_ID]}"


def test_webhook_hash_matches_the_tablet() -> None:
    # Same value HaBleApiHandler.webhookHash gives (sha256, first 16 hex): the hand-off loop stops on a match.
    assert webhook_hash("abc") == "ba7816bf8f01cfea"
