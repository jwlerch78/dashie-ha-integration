"""Bluetooth for HA, connections: the client HA's Bluetooth stack uses to connect THROUGH a tablet.

HA gives each proxy a connector (``HaBluetoothConnector``): a bleak client class plus a
"can it connect now?" check. When an integration connects to a device the tablet hears
best, HA instantiates this client, and every connect/read/write/subscribe becomes a
request over the tablet's channel (``ble_channel.py``), carried out there with Android's
own Bluetooth (HaBleGattBridge.kt). The ESPHome proxy's client (bleak-esphome) is the
model; this one is much smaller: no pairing, no service cache.

Requires bleak 1.0+ (HA 2025.8+); older HA never marks the tablet connectable
(``CONNECT_SUPPORTED``).
"""
from __future__ import annotations

import inspect
import logging
from functools import partial
from typing import Any

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.client import BaseBleakClient, NotifyCallback
from bleak.backends.descriptor import BleakGATTDescriptor
from bleak.backends.device import BLEDevice
from bleak.backends.service import BleakGATTService, BleakGATTServiceCollection
from bleak.exc import BleakError
from homeassistant.components.bluetooth import HaBluetoothConnector

from .ble_channel import BleChannel, ChannelError

_LOGGER = logging.getLogger(__name__)

# bleak 1.0 gave BaseBleakClient.connect its `pair` argument; reading the signature avoids package metadata, which is
# file I/O and was flagged as a blocking call when this module was first imported inside HA's event loop (10-06).
CONNECT_SUPPORTED = "pair" in inspect.signature(BaseBleakClient.connect).parameters

DEFAULT_TIMEOUT = 20.0
OP_TIMEOUT = 15.0
ATT_HEADER = 3


class DashieBleClient(BaseBleakClient):
    """One connection to one device, made by the tablet."""

    def __init__(self, address_or_ble_device: BLEDevice | str, *args: Any, channel: BleChannel, **kwargs: Any) -> None:
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
        super().__init__(address_or_ble_device, *args, **kwargs)
        self._channel = channel
        self._connected = False
        self._mtu = 23

    def __str__(self) -> str:
        return f"Dashie {self._channel.name}: {self.address}"

    @property
    def mtu_size(self) -> int:
        return self._mtu

    @property
    def is_connected(self) -> bool:
        return self._connected and self._channel.connected

    async def connect(self, pair: bool, **kwargs: Any) -> None:
        if pair:
            raise NotImplementedError("Pairing through a Dashie tablet is not supported")
        timeout = float(kwargs.get("timeout", self._timeout))
        self._channel.on_disconnect(self.address, self._on_disconnected)
        try:
            res = await self._channel.request("connect", timeout + 2, a=self.address, timeout=int(timeout * 1000))
        except ChannelError as err:
            self._channel.on_disconnect(self.address, None)
            raise BleakError(str(err)) from err
        self._mtu = int(res.get("mtu") or 23)
        self.services = build_services(res.get("services") or [], lambda: self._mtu - ATT_HEADER)
        self._connected = True
        _LOGGER.debug("%s: connected, %d services", self, len(self.services.services))

    async def disconnect(self) -> None:
        if not self._connected:
            return
        self._connected = False
        self._channel.on_disconnect(self.address, None)
        try:
            await self._channel.request("disconnect", OP_TIMEOUT, a=self.address)
        except ChannelError as err:
            _LOGGER.debug("%s: disconnect: %s", self, err)

    async def pair(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("Pairing through a Dashie tablet is not supported")

    async def unpair(self) -> None:
        raise NotImplementedError("Pairing through a Dashie tablet is not supported")

    async def read_gatt_char(self, characteristic: BleakGATTCharacteristic, **kwargs: Any) -> bytearray:
        return bytearray.fromhex((await self._op("read", h=characteristic.handle)).get("d") or "")

    async def read_gatt_descriptor(self, descriptor: BleakGATTDescriptor, **kwargs: Any) -> bytearray:
        return bytearray.fromhex((await self._op("readDesc", h=descriptor.handle)).get("d") or "")

    async def write_gatt_char(self, characteristic: BleakGATTCharacteristic, data: Any, response: bool) -> None:
        await self._op("write", h=characteristic.handle, d=bytes(data).hex(), rsp=bool(response))

    async def write_gatt_descriptor(self, descriptor: BleakGATTDescriptor, data: Any) -> None:
        await self._op("writeDesc", h=descriptor.handle, d=bytes(data).hex())

    async def start_notify(self, characteristic: BleakGATTCharacteristic, callback: NotifyCallback, **kwargs: Any) -> None:
        self._channel.on_notify(self.address, characteristic.handle, lambda data: callback(bytearray(data)))
        try:
            await self._op("notify", h=characteristic.handle, on=True)
        except BleakError:
            self._channel.on_notify(self.address, characteristic.handle, None)
            raise

    async def stop_notify(self, characteristic: BleakGATTCharacteristic) -> None:
        self._channel.on_notify(self.address, characteristic.handle, None)
        await self._op("notify", h=characteristic.handle, on=False)

    async def _op(self, op: str, **args: Any) -> dict[str, Any]:
        if not self.is_connected:
            raise BleakError(f"{self}: not connected")
        try:
            return await self._channel.request(op, OP_TIMEOUT, a=self.address, **args)
        except ChannelError as err:
            raise BleakError(str(err)) from err

    def _on_disconnected(self) -> None:
        was = self._connected
        self._connected = False
        if was and self._disconnected_callback is not None:
            self._disconnected_callback()


def build_services(raw: list[dict[str, Any]], max_write: Any) -> BleakGATTServiceCollection:
    """The tablet's service list → bleak's. Handles are the tablet's, unique per connection.

    Wire: [{"u": uuid, "h": int, "c": [{"u": uuid, "h": int, "p": ["read", "notify", ...],
                                       "d": [{"u": uuid, "h": int}]}]}]
    """
    services = BleakGATTServiceCollection()
    for s in raw:
        service = BleakGATTService(None, int(s["h"]), str(s["u"]).lower())
        services.add_service(service)
        for c in s.get("c") or []:
            char = BleakGATTCharacteristic(None, int(c["h"]), str(c["u"]).lower(), list(c.get("p") or []), max_write, service)
            services.add_characteristic(char)
            for d in c.get("d") or []:
                services.add_descriptor(BleakGATTDescriptor(None, int(d["h"]), str(d["u"]).lower(), char))
    return services


def connector(source: str, channel: BleChannel) -> HaBluetoothConnector:
    """What HA needs to route a connection through this tablet."""
    return HaBluetoothConnector(
        client=partial(DashieBleClient, channel=channel),
        source=source,
        can_connect=lambda: channel.connected and channel.free > 0,
    )
