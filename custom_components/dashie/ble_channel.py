"""Bluetooth for HA, connections spike: a live two-way channel between HA and one tablet.

Scanning needs only the tablet's one-way webhook posts. A Bluetooth CONNECTION needs HA to
ask the tablet to connect, read, write and subscribe, and to hear back within seconds
(HA's connection retries time out in ~10-20 s), plus the tablet pushing notifications
and disconnects as they happen. So the tablet opens a WebSocket to this view and keeps
it open while "Let Home Assistant connect" is on.

The URL carries the entry's Bluetooth webhook id, the same credential the webhook uses
(``ble_webhook.py``), and is LAN-only like the webhook.

Wire (JSON text frames; contract with the app's HaBleChannel.kt, fixture
``tests/fixtures/ble-connect-wire.json``)::

    tablet → HA  {"t": "hello", "v": 1, "slots": 3, "free": 3}
    HA → tablet  {"t": "req", "id": 7, "op": "connect", "a": "AA:..", "timeout": 20000}
    tablet → HA  {"t": "res", "id": 7, "ok": true, "mtu": 23, "services": [...]}
    tablet → HA  {"t": "res", "id": 8, "ok": false, "err": "GATT status 133"}
    tablet → HA  {"t": "ev", "ev": "notify", "a": "AA:..", "h": 12, "d": "0a0b"}
    tablet → HA  {"t": "ev", "ev": "disconnected", "a": "AA:..", "reason": "…"}
    tablet → HA  {"t": "ev", "ev": "slots", "free": 2}

Ops: connect, disconnect, read, readDesc, write, writeDesc, notify (see ble_connect.py).
"""
from __future__ import annotations

import asyncio
import ipaddress
import itertools
import json
import logging
from collections.abc import Callable
from typing import Any

from aiohttp import WSMsgType, web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.util.network import is_local

_LOGGER = logging.getLogger(__name__)

CHANNEL_VERSION = 1
DATA_CHANNELS = "dashie_ble_channels"  # hass.data: webhook_id -> BleChannel
URL = "/api/dashie/ble_channel/{webhook_id}"


class ChannelError(Exception):
    """The tablet refused a request, the request timed out, or the channel is gone."""


class BleChannel:
    """The open channel to one tablet, or none (``connected`` False)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._ws: web.WebSocketResponse | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notify: dict[tuple[str, int], Callable[[bytes], None]] = {}
        self._disconnect: dict[str, Callable[[], None]] = {}
        self.slots = 0
        self.free = 0

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def request(self, op: str, wait: float, **args: Any) -> dict[str, Any]:
        """Send one request; return the tablet's result, or raise ChannelError."""
        ws = self._ws
        if ws is None or ws.closed:
            raise ChannelError(f"{self.name}: no channel to the tablet")
        req_id = next(self._ids)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        try:
            await ws.send_str(json.dumps({"t": "req", "id": req_id, "op": op, **args}))
            res = await asyncio.wait_for(fut, wait)
        except TimeoutError as err:
            raise ChannelError(f"{self.name}: {op} timed out after {wait:.0f} s") from err
        finally:
            self._pending.pop(req_id, None)
        if not res.get("ok"):
            raise ChannelError(f"{self.name}: {op} failed: {res.get('err') or 'no reason given'}")
        return res

    def on_notify(self, address: str, handle: int, cb: Callable[[bytes], None] | None) -> None:
        if cb is None:
            self._notify.pop((address, handle), None)
        else:
            self._notify[(address, handle)] = cb

    def on_disconnect(self, address: str, cb: Callable[[], None] | None) -> None:
        if cb is None:
            self._disconnect.pop(address, None)
        else:
            self._disconnect[address] = cb

    @callback
    def handle_message(self, msg: dict[str, Any]) -> None:
        kind = msg.get("t")
        if kind == "res":
            fut = self._pending.get(msg.get("id"))
            if fut is not None and not fut.done():
                fut.set_result(msg)
            elif fut is None:
                _LOGGER.warning("DROP: %s answered unknown request %s", self.name, msg.get("id"))
        elif kind == "ev":
            self._handle_event(msg)
        elif kind == "hello":
            self.slots = int(msg.get("slots") or 0)
            self.free = int(msg.get("free") or 0)
            _LOGGER.info("SMARTHOME_BLE_CHANNEL hello from %s v=%s slots=%s", self.name, msg.get("v"), self.slots)
        else:
            _LOGGER.warning("DROP: %s sent a channel message of unknown type %r", self.name, kind)

    def _handle_event(self, msg: dict[str, Any]) -> None:
        ev, address = msg.get("ev"), str(msg.get("a") or "").upper()
        if ev == "notify":
            handle = msg.get("h")
            cb = self._notify.get((address, handle)) if isinstance(handle, int) else None
            if cb is None:
                _LOGGER.debug("%s: notification for %s handle %s with no subscriber", self.name, address, msg.get("h"))
                return
            try:
                cb(bytes.fromhex(msg.get("d") or ""))
            except ValueError:
                _LOGGER.warning("DROP: %s sent unreadable notification data for %s", self.name, address)
        elif ev == "disconnected":
            if (cb := self._disconnect.pop(address, None)) is not None:
                cb()
        elif ev == "slots":
            self.free = int(msg.get("free") or 0)
        else:
            _LOGGER.warning("DROP: %s sent unknown channel event %r", self.name, ev)

    @callback
    def attach(self, ws: web.WebSocketResponse) -> None:
        self._ws = ws

    @callback
    def detach(self, ws: web.WebSocketResponse) -> None:
        """The socket closed: fail what is in flight and tell every connection it is gone."""
        if self._ws is not ws:
            return
        self._ws = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ChannelError(f"{self.name}: channel closed"))
        for cb in list(self._disconnect.values()):
            cb()
        self._disconnect.clear()
        self._notify.clear()
        self.slots = self.free = 0


class BleChannelView(HomeAssistantView):
    """``/api/dashie/ble_channel/<webhook_id>``: where a tablet opens its channel."""

    url = URL
    name = "api:dashie:ble_channel"
    requires_auth = False  # the webhook id is the credential, as for the webhook itself

    async def get(self, request: web.Request, webhook_id: str) -> web.StreamResponse:
        hass: HomeAssistant = request.app["hass"]
        channel: BleChannel | None = hass.data.get(DATA_CHANNELS, {}).get(webhook_id)
        if channel is None:
            return web.Response(status=404)
        try:
            remote = ipaddress.ip_address(request.remote or "")
        except ValueError:
            return web.Response(status=403)
        if not is_local(remote):
            _LOGGER.warning("DROP: Bluetooth channel for %s refused from non-local %s", channel.name, remote)
            return web.Response(status=403)

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        if channel.connected:
            _LOGGER.info("SMARTHOME_BLE_CHANNEL %s reconnected; replacing the old socket", channel.name)
            channel.detach(channel._ws)  # noqa: SLF001
        channel.attach(ws)
        _LOGGER.info("SMARTHOME_BLE_CHANNEL open %s", channel.name)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    _LOGGER.warning("DROP: %s sent a non-JSON channel frame", channel.name)
                    continue
                if isinstance(data, dict):
                    channel.handle_message(data)
        finally:
            channel.detach(ws)
            _LOGGER.info("SMARTHOME_BLE_CHANNEL closed %s", channel.name)
        return ws


@callback
def async_register_channel(hass: HomeAssistant, webhook_id: str, name: str) -> BleChannel:
    """One channel slot per entry; the view is registered once for all of them."""
    channels: dict[str, BleChannel] = hass.data.setdefault(DATA_CHANNELS, {})
    if not hass.data.get(f"{DATA_CHANNELS}_view"):
        hass.http.register_view(BleChannelView())
        hass.data[f"{DATA_CHANNELS}_view"] = True
    channel = channels[webhook_id] = BleChannel(name)
    return channel


@callback
def async_unregister_channel(hass: HomeAssistant, webhook_id: str) -> None:
    channel = hass.data.get(DATA_CHANNELS, {}).pop(webhook_id, None)
    if channel is not None and channel.connected:
        hass.async_create_task(channel._ws.close())  # noqa: SLF001
