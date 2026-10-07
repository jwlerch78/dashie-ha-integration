"""Bluetooth for HA: a tablet's adverts reach HA's Bluetooth manager through the webhook.

The end-to-end test is the integration half of the plan's "done": an advert posted to
the entry's webhook comes out of HA's Bluetooth API with the tablet's scanner as its
source. Every absence asserted here is paired with the same lookup succeeding.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aioresponses import aioresponses
from homeassistant.components import bluetooth
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dashie.registry_compat import all_devices
from custom_components.dashie.ble_scanner import _parse_advert, scanner_source
from custom_components.dashie.ble_webhook import CMD_SET_WEBHOOK, CONF_BLE_WEBHOOK_ID, webhook_hash

DOMAIN = "dashie"
DEVICE_ID = "a83e167a70e648255f71a1744d25f740"
IPV4 = "192.168.23.96"
BASE = f"http://{IPV4}:2323"
INKBIRD = "49:24:03:27:01:C7"
UNHEARD = "A4:C1:38:00:00:01"
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
    # Two of HA's Bluetooth devices: the tablet will hear one of them, nobody hears the other.
    devices = dr.async_get(hass)
    devices.async_get_or_create(config_entry_id=entry.entry_id, name="IBS-TH2",
                                connections={(dr.CONNECTION_BLUETOOTH, INKBIRD.lower())})
    renamed = devices.async_get_or_create(config_entry_id=entry.entry_id, name="Govee H5075",
                                          connections={(dr.CONNECTION_BLUETOOTH, UNHEARD)})
    devices.async_update_device(renamed.id, name_by_user="Garage")

    resp = await client.post(url, json={"v": 1, "enabled": True, "adverts": [ADVERT, {"bad": 1}]})
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "ok" and body["accepted"] == 1
    assert "config" in body and body["config"]["v"] == 1
    # HA's names for its devices, the user's rename winning; addresses upper-cased as the tablet sees them.
    # HA's own adapter (enable_bluetooth's hci0) is in the registry with a Bluetooth address and is left out.
    assert any(kind == dr.CONNECTION_BLUETOOTH for d in all_devices(devices)
               if d.config_entries & {e.entry_id for e in hass.config_entries.async_entries("bluetooth")}
               for kind, _ in d.connections)
    assert body["config"]["names"] == {INKBIRD: "IBS-TH2", UNHEARD: "Garage"}
    assert body["config"]["addresses"] == sorted([INKBIRD, UNHEARD])
    # Heard through this tablet; the unheard device is absent, not "other".
    assert body["heardVia"] == {INKBIRD: "this"}

    # The same device, best heard by another receiver.
    elsewhere = MagicMock(source="AA:BB:CC:DD:EE:FF")
    with patch.object(bluetooth, "async_last_service_info",
                      side_effect=lambda h, a, connectable: elsewhere if a == INKBIRD else None):
        resp = await client.post(url, json={"adverts": [ADVERT], "configHash": body["configHash"]})
    assert (await resp.json())["heardVia"] == {INKBIRD: "other"}

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
    [
        ({"supported": True, "webhookHash": ""}, True),
        ("MATCH", False),  # the tablet already holds this webhook: the loop must stop
        (None, False),
        ({"supported": False}, False),
    ],
)
async def test_handoff_only_to_tablets_that_support_it(hass: HomeAssistant, ha_ble, expect_handoff) -> None:
    # Device info is fed through async_set_updated_data (what a successful poll does), NOT an HTTP mock:
    # aioresponses cannot mock aiohttp 3.14 (HA 2026.9), which would make the negative cases pass vacuously.
    with patch(
        "custom_components.dashie.coordinator.DashieCoordinator.async_send",
        AsyncMock(return_value=type("R", (), {"ok": True})()),
    ) as send:
        entry = await _setup_entry(hass)
        webhook_id = entry.data[CONF_BLE_WEBHOOK_ID]
        info = {"deviceID": DEVICE_ID, "deviceName": "Kitchen"}
        if ha_ble == "MATCH":
            info["haBle"] = {"supported": True, "webhookHash": webhook_hash(webhook_id)}
        elif ha_ble is not None:
            info["haBle"] = ha_ble
        coordinator = hass.data[DOMAIN][entry.entry_id]
        coordinator.async_set_updated_data(info)
        await hass.async_block_till_done()
        # Positive control for every case: the listener ran on this data (a supported, mismatched copy hands off).
        probe = dict(info, haBle={"supported": True, "webhookHash": "0" * 16})
        calls_before_probe = [c for c in send.call_args_list if c.args and c.args[0] == CMD_SET_WEBHOOK]
        if not expect_handoff:
            coordinator.async_set_updated_data(probe)
            await hass.async_block_till_done()
            probed = [c for c in send.call_args_list if c.args and c.args[0] == CMD_SET_WEBHOOK]
            assert len(probed) == 1, "the listener never ran, so the negative result would be vacuous"
    assert bool(calls_before_probe) == expect_handoff
    if expect_handoff:
        assert calls_before_probe[0].kwargs["webhookId"] == webhook_id
        assert calls_before_probe[0].kwargs["path"] == f"/api/webhook/{webhook_id}"

def test_webhook_hash_matches_the_tablet() -> None:
    # Same value HaBleApiHandler.webhookHash gives (sha256, first 16 hex): the hand-off loop stops on a match.
    assert webhook_hash("abc") == "ba7816bf8f01cfea"


# --- Contract row 178: one fixture, byte-identical in the Android repo -------------------

import hashlib  # noqa: E402
import json  # noqa: E402
import pathlib  # noqa: E402

from custom_components.dashie.ble_matchers import _MATCHER_KEYS, CONFIG_VERSION  # noqa: E402

# Same constant as Android BleWireContractTest. Re-copy the file rather than "fixing" this.
_FIXTURE_SHA256 = "e487193551b3dc464549ebe7e28e10096c3ae241c864c9186c76cbfe4616aefb"


def _fixture() -> dict:
    raw = (pathlib.Path(__file__).parent / "fixtures" / "ble-wire.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _FIXTURE_SHA256, "ble-wire.json differs from the Android copy"
    return json.loads(raw)


def test_fixture_batch_is_what_the_webhook_reads() -> None:
    adverts = _fixture()["batch"]["adverts"]
    parsed = [_parse_advert(a) for a in adverts]
    assert all(p is not None for p in parsed)
    assert parsed[0][2] == "tps" and parsed[0][5] == {10241: b"\x0a\x0b\x0c"}
    assert parsed[1][4] == {"0000fcd2-0000-1000-8000-00805f9b34fb": b"\x40\x00"} and parsed[1][6] == -4


def test_fixture_config_is_what_ble_matchers_sends() -> None:
    config = _fixture()["config"]
    assert config["v"] == CONFIG_VERSION
    for matcher in config["matchers"]:
        assert set(matcher) - {"domain"} <= set(_MATCHER_KEYS), matcher
    assert set(config["names"]) <= set(config["addresses"])
    assert set(_fixture()["reply"]["heardVia"].values()) <= {"this", "other"}


async def test_bluetooth_devices_sensor_counts_only_what_this_tablet_is_best_for(
    hass: HomeAssistant, enable_bluetooth, hass_client_no_auth
) -> None:
    """The sensor's state is how many devices HA uses THIS tablet for, not how many it hears.

    The fixture is built so the two are different numbers: the tablet hears both devices,
    and HA prefers another receiver for one of them. A sensor that reported everything it
    heard — or marked every device "this" — reads 2 here and fails.

    `via` is the field with something to get wrong, so it is asserted per address rather
    than only through the count.
    """
    from custom_components.dashie.ble_entities import DashieBluetoothDevicesSensor

    entry = await _setup_entry(hass)
    webhook_id = entry.data[CONF_BLE_WEBHOOK_ID]
    client = await hass_client_no_auth()
    url = f"/api/webhook/{webhook_id}"

    devices = dr.async_get(hass)
    devices.async_get_or_create(config_entry_id=entry.entry_id, name="IBS-TH2",
                                connections={(dr.CONNECTION_BLUETOOTH, INKBIRD.lower())})
    renamed = devices.async_get_or_create(config_entry_id=entry.entry_id, name="Govee H5075",
                                          connections={(dr.CONNECTION_BLUETOOTH, UNHEARD)})
    devices.async_update_device(renamed.id, name_by_user="Garage")

    coordinator = hass.data[DOMAIN][entry.entry_id]
    sensor = DashieBluetoothDevicesSensor(coordinator, DEVICE_ID, entry)
    sensor.hass = hass

    # Both devices are heard; HA's best receiver for UNHEARD is a different one.
    ours = MagicMock(source=scanner_source(DEVICE_ID), rssi=-61)
    theirs = MagicMock(source="AA:BB:CC:DD:EE:FF", rssi=-88)
    with patch.object(bluetooth, "async_last_service_info",
                      side_effect=lambda h, a, connectable: ours if a == INKBIRD else theirs):
        resp = await client.post(url, json={"v": 1, "enabled": True, "adverts": [ADVERT]})
    assert resp.status == 200

    # The count is the state, and it counts "this" only.
    assert sensor.native_value == 1

    # Availability rides the coordinator as well as the scanner, and that is deliberate:
    # the BLE view only moves when a batch arrives, so a tablet HA can no longer reach
    # would otherwise keep presenting its last count as current. Driven explicitly here
    # rather than inherited from fixture timing, so the property is tested and not assumed.
    coordinator.last_update_success = True
    assert sensor.available is True
    coordinator.last_update_success = False
    assert sensor.available is False, "a tablet HA cannot reach must not show a frozen count as current"
    coordinator.last_update_success = True

    # The attribute carries every device the tablet hears, including the one HA prefers elsewhere.
    reported = sensor.extra_state_attributes["devices"]
    assert {d["address"]: d["via"] for d in reported} == {INKBIRD: "this", UNHEARD: "other"}
    assert {d["address"]: d["name"] for d in reported} == {INKBIRD: "IBS-TH2", UNHEARD: "Garage"}
    assert {d["address"]: d["rssi"] for d in reported} == {INKBIRD: -61, UNHEARD: -88}

    # The sensor and the tablet are told the same thing, because one pass produced both.
    assert (await resp.json())["heardVia"] == {INKBIRD: "this", UNHEARD: "other"}

    # Bluetooth for HA turned off is unavailable — distinct from scanning and hearing nothing.
    resp = await client.post(url, json={"enabled": False})
    assert resp.status == 200
    assert sensor.available is False
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes["devices"] == []


async def test_connect_request_is_honoured_only_where_ha_supports_it(hass: HomeAssistant, enable_bluetooth, hass_client_no_auth) -> None:
    """A tablet asking to be connectable gets it on HA with bleak 1.0+, and stays listen-only on older HA."""
    from custom_components.dashie.ble_connect import CONNECT_SUPPORTED

    entry = await _setup_entry(hass)
    client = await hass_client_no_auth()
    url = f"/api/webhook/{entry.data[CONF_BLE_WEBHOOK_ID]}"
    body = await (await client.post(url, json={"adverts": [ADVERT], "connect": True})).json()
    assert body["connectSupported"] is CONNECT_SUPPORTED
    assert bluetooth.async_scanner_by_source(hass, scanner_source(DEVICE_ID)).connectable is CONNECT_SUPPORTED
