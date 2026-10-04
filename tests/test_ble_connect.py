"""Bluetooth for HA, connections spike: HA connects to a device THROUGH a tablet.

Drives the real channel view over a real WebSocket (HA's test HTTP server), playing the
tablet's side with the frames in fixtures/ble-connect-wire.json (shared byte-for-byte
with the app's BleConnectWireContractTest). Each "it refuses" assertion is paired with
the same call succeeding.

Needs bleak 1.0+ (HA 2025.8+): skipped on older HA, where the tablet is never connectable.
"""
import asyncio
import hashlib
import json
import pathlib

import pytest
from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from custom_components.dashie.ble_connect import CONNECT_SUPPORTED
from custom_components.dashie.ble_scanner import scanner_source
from custom_components.dashie.ble_webhook import CONF_BLE_WEBHOOK_ID

from .test_ble import ADVERT, DEVICE_ID, _setup_entry

pytestmark = pytest.mark.skipif(not CONNECT_SUPPORTED, reason="bleak < 1.0: connections not offered")

_FIXTURE_SHA256 = "ae6ac87b7a07d8169da26452b5fc9b0916b5c761e8c90e551454d0b6e45cea9a"
ADDR = "AA:BB:CC:DD:EE:01"
BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"
CUSTOM = "0000ffe1-0000-1000-8000-00805f9b34fb"


def _wire() -> dict:
    raw = (pathlib.Path(__file__).parent / "fixtures" / "ble-connect-wire.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _FIXTURE_SHA256, "ble-connect-wire.json differs from the Android copy"
    return json.loads(raw)


async def _answer(ws, expected_op: str, response: dict) -> dict:
    """Read HA's next request, check its op, answer it with ``response`` under its id."""
    req = await asyncio.wait_for(ws.receive_json(), 5)
    assert req["t"] == "req" and req["op"] == expected_op, req
    await ws.send_json({**response, "id": req["id"]})
    return req


async def test_connect_read_notify_disconnect_through_the_tablet(hass: HomeAssistant, enable_bluetooth, hass_client_no_auth) -> None:
    wire = _wire()
    entry = await _setup_entry(hass)
    webhook_id = entry.data[CONF_BLE_WEBHOOK_ID]
    http = await hass_client_no_auth()
    source = scanner_source(DEVICE_ID)

    # Listening only: the scanner is not connectable, and the reply says HA could route connections.
    body = await (await http.post(f"/api/webhook/{webhook_id}", json={"adverts": [ADVERT]})).json()
    assert body["connectSupported"] is True
    assert bluetooth.async_scanner_by_source(hass, source).connectable is False

    # "Let Home Assistant connect" on: re-registered connectable, but no channel yet ⇒ cannot connect.
    await http.post(f"/api/webhook/{webhook_id}", json={"adverts": [ADVERT], "connect": True})
    scanner = bluetooth.async_scanner_by_source(hass, source)
    assert scanner.connectable is True
    assert scanner.connector.can_connect() is False

    ws = await http.ws_connect(f"/api/dashie/ble_channel/{webhook_id}")
    await ws.send_json(wire["hello"])
    await asyncio.sleep(0.05)
    assert scanner.connector.can_connect() is True

    disconnected = []
    device = BLEDevice(ADDR, "LightBlue", {"source": source})
    client = scanner.connector.client(device, disconnected_callback=lambda: disconnected.append(1))

    # connect: HA asks, the tablet answers with the device's services.
    task = hass.async_create_task(client.connect(pair=False))
    req = await _answer(ws, "connect", wire["responses"]["connect"])
    assert req["a"] == ADDR and req["timeout"] > 0
    await task
    assert client.is_connected and client.mtu_size == 185
    assert client.services.get_characteristic(BATTERY).handle == 3
    assert client.services.get_characteristic(CUSTOM).properties == ["read", "write-without-response", "write", "notify"]

    # read: by handle, value comes back as bytes.
    task = hass.async_create_task(client.read_gatt_char(client.services.get_characteristic(BATTERY)))
    req = await _answer(ws, "read", wire["responses"]["read"])
    assert req["h"] == 3
    assert await task == bytearray(b"\x5a")

    # write with response.
    task = hass.async_create_task(client.write_gatt_char(client.services.get_characteristic(CUSTOM), b"\x01\x02", True))
    req = await _answer(ws, "write", wire["responses"]["write"])
    assert (req["h"], req["d"], req["rsp"]) == (5, "0102", True)
    await task

    # notify: subscribed notifications arrive; one for another handle does not.
    got = []
    task = hass.async_create_task(client.start_notify(client.services.get_characteristic(CUSTOM), got.append))
    await _answer(ws, "notify", wire["responses"]["write"])
    await task
    await ws.send_json({**wire["events"]["notify"], "h": 99})
    await ws.send_json(wire["events"]["notify"])
    await asyncio.sleep(0.05)
    assert got == [bytearray(b"\x0a\x0b")]

    # The device drops the link: bleak's disconnected callback fires, the client knows.
    await ws.send_json(wire["events"]["disconnected"])
    await asyncio.sleep(0.05)
    assert disconnected == [1] and not client.is_connected

    # A refused connect surfaces as a BleakError carrying the tablet's reason.
    task = hass.async_create_task(client.connect(pair=False))
    await _answer(ws, "connect", wire["responses"]["failed"])
    with pytest.raises(Exception, match="133"):
        await task

    # The channel closes: the tablet can no longer be offered for connections.
    await ws.close()
    await asyncio.sleep(0.05)
    assert scanner.connector.can_connect() is False

    # Turned back off: listening-only again.
    await http.post(f"/api/webhook/{webhook_id}", json={"adverts": [ADVERT], "connect": False})
    assert bluetooth.async_scanner_by_source(hass, source).connectable is False


async def test_channel_needs_the_entrys_webhook_id(hass: HomeAssistant, enable_bluetooth, hass_client_no_auth) -> None:
    entry = await _setup_entry(hass)
    http = await hass_client_no_auth()
    resp = await http.get("/api/dashie/ble_channel/not-a-webhook-id")
    assert resp.status == 404
    ws = await http.ws_connect(f"/api/dashie/ble_channel/{entry.data[CONF_BLE_WEBHOOK_ID]}")
    assert not ws.closed
    await ws.close()
