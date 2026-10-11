"""🔴 THE CONTROL ON OUR OWN INSTRUMENT. Read `fake_http.py`'s header first.

`fake_http.FakeSession` replaced `aioresponses` because aioresponses reaches into
aiohttp's internals and aiohttp changed them. But a fake we write can be wrong in the
direction that makes tests pass, and **a green suite on a dishonest fake is worse than
an honest red one** — it is trap 13 wearing the subject's clothes.

So this file drives REAL integration code (`DashieCoordinator.async_send`) over the same
scenarios twice — once against a real `aiohttp` server on localhost, once against the
fake — and requires the observable outcome to be IDENTICAL. The fake is only ever as
trustworthy as this file is thorough.

⚠️ If you add behavior to `fake_http.py`, add a SHAPES row here that pins it. A fidelity
control that lags the fake it guards is decoration.

What this proves: the fake reproduces aiohttp's behavior **for the paths these scenarios
cover**. What it does not prove: anything about paths they don't, nor that the device
behaves as the scenarios claim. That is device proof and stays device proof.
"""
from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dashie.commands import NO_RESPONSE, REFUSED
from custom_components.dashie.coordinator import DashieCoordinator

from .fake_http import FakeResponse, FakeSession

DOMAIN = "dashie"
REFUSAL = {"status": "ERROR", "message": "Reboot failed: requires root"}

# (cmd, status, body, content_type, expected (ok, kind, reason))
# The cmd doubles as the routing key: the real server switches on ?cmd=, and the fake
# matches the same substring, so BOTH arms are driven by this one table.
SHAPES = [
    ("ok200", 200, '{"status": "OK"}', "application/json", (True, None, "")),
    ("empty200", 200, "", "application/json", (True, None, "")),
    ("text200", 200, "hello", "text/plain", (True, None, "")),
    ("malformed200", 200, "{not json", "application/json", (True, None, "")),
    ("refusal400", 400, None, "application/json",
     (False, REFUSED, "Reboot failed: requires root")),
    ("refusal401", 401, None, "application/json",
     (False, REFUSED, "Reboot failed: requires root")),
    # The reason-phrase case. async_send formats f"HTTP {err.status} {err.message}", so a
    # fake that omits aiohttp's reason phrase diverges here and nowhere else.
    ("html500", 500, "<html>no</html>", "text/html",
     (False, REFUSED, "HTTP 500 Internal Server Error")),
    # A SECOND error status, deliberately: with only 500 in the table, a fake that
    # hardcoded `status=500` in raise_for_status passed the whole control. Found by
    # fault injection (W6), not by review.
    ("html503", 503, "<html>busy</html>", "text/html",
     (False, REFUSED, "HTTP 503 Service Unavailable")),
]
IDS = [s[0] for s in SHAPES]


async def _real_server(cases) -> TestServer:
    """A real aiohttp server answering each scenario by its ?cmd= value."""
    async def handler(request: web.Request) -> web.Response:
        cmd = request.query.get("cmd", "")
        for name, status, body, ctype, _ in cases:
            if name == cmd:
                text = '{"status": "ERROR", "message": "Reboot failed: requires root"}' \
                    if body is None else body
                return web.Response(status=status, text=text, content_type=ctype)
        return web.Response(status=404, text="{}", content_type="application/json")

    app = web.Application()
    app.router.add_get("/", handler)
    server = TestServer(app)
    await server.start_server()
    return server


def _coordinator(hass: HomeAssistant, host: str, port: int) -> DashieCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=f"{host}:{port}",
                            data={"host": host, "port": port})
    entry.add_to_hass(hass)
    return DashieCoordinator(hass, host, port, "", config_entry=entry)


def _as_tuple(result) -> tuple:
    return (result.ok, result.kind, result.reason)


@pytest.mark.parametrize("name,status,body,ctype,expected", SHAPES, ids=IDS)
async def test_the_fake_matches_a_real_server(
    hass: HomeAssistant, socket_enabled, name, status, body, ctype, expected
) -> None:
    """Same production call, real server vs fake — the outcomes must be identical."""
    server = await _real_server(SHAPES)
    try:
        real_coord = _coordinator(hass, server.host, server.port)
        real = _as_tuple(await real_coord.async_send(name))
        await real_coord.async_shutdown()
    finally:
        await server.close()

    fake_coord = _coordinator(hass, "192.168.23.96", 2323)
    payload = REFUSAL if body is None else None
    fake_coord._session = FakeSession({
        f"cmd={name}": FakeResponse(status=status, body=body, payload=payload,
                                    content_type=ctype),
    })
    fake = _as_tuple(await fake_coord.async_send(name))

    assert fake == real, (
        f"{name}: the fake and a real aiohttp server disagree.\n"
        f"  real server: {real}\n  fake:        {fake}\n"
        "The fake is wrong, or aiohttp changed. Fix the fake — never the assertion."
    )
    assert real == expected, (
        f"{name}: the REAL server no longer behaves as this table claims ({real} != "
        f"{expected}). Then the table is stale, not the fake."
    )


# (cmd-less) deviceInfo scenarios: body, content_type, and whether the fetch SUCCEEDS.
# This path calls `response.json()` *with* aiohttp's content-type check, which
# `async_send` never exercises because it passes content_type=None inside a
# try/except ValueError. Without these rows the control was blind to a fake that
# skipped the content-type check (W3) or returned None instead of raising (W4).
INFO_SHAPES = [
    ("info ok", 200, '{"deviceID": "abc", "deviceName": "Kitchen"}', "application/json", True),
    ("info html", 200, "<html>nope</html>", "text/html", False),
    ("info malformed", 200, "{not json", "application/json", False),
    ("info 500", 500, "<html>no</html>", "text/html", False),
]


@pytest.mark.parametrize("name,status,body,ctype,should_succeed", INFO_SHAPES,
                         ids=[s[0] for s in INFO_SHAPES])
async def test_the_fake_matches_a_real_server_on_the_deviceinfo_path(
    hass: HomeAssistant, socket_enabled, name, status, body, ctype, should_succeed
) -> None:
    """The OTHER production path — `_fetch_device_info`, which checks content type."""
    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=status, text=body, content_type=ctype)

    app = web.Application()
    app.router.add_get("/", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        real_coord = _coordinator(hass, server.host, server.port)
        real = await _outcome(real_coord)
        await real_coord.async_shutdown()
    finally:
        await server.close()

    fake_coord = _coordinator(hass, "192.168.23.96", 2323)
    fake_coord._session = FakeSession(
        {"cmd=": FakeResponse(status=status, body=body, content_type=ctype)})
    fake = await _outcome(fake_coord)

    assert fake == real, (
        f"{name}: fake and real server disagree on the deviceInfo path.\n"
        f"  real server: {real}\n  fake:        {fake}\n"
        "Fix the fake — never the assertion."
    )
    assert (real[0] == "ok") is should_succeed, (
        f"{name}: the REAL server no longer behaves as this table claims ({real}); "
        "then the table is stale, not the fake."
    )


async def _outcome(coord) -> tuple:
    """Reduce _fetch_device_info to a comparable (kind, exception-type) pair."""
    try:
        data = await coord._fetch_device_info()
    except Exception as err:  # noqa: BLE001 - the TYPE is the observable here
        return ("raised", type(err).__name__)
    return ("ok", str(data.get("deviceID")))


async def test_the_fake_reproduces_a_timeout(hass: HomeAssistant) -> None:
    """Timeouts are a raised exception, not a response — pinned separately.

    No real-server arm: making a real server hang would add seconds to every run for a
    path whose only observable is the exception type, which the fake raises directly.
    """
    coord = _coordinator(hass, "192.168.23.96", 2323)
    coord._session = FakeSession({"cmd=": asyncio.TimeoutError()})
    assert _as_tuple(await coord.async_send("rebootDevice")) == (
        False, NO_RESPONSE, "TimeoutError",
    )


async def test_an_unrouted_request_fails_loudly(hass: HomeAssistant) -> None:
    """A fake that silently answers 200 to anything passes tests it never exercised."""
    coord = _coordinator(hass, "192.168.23.96", 2323)
    coord._session = FakeSession({"cmd=expected": FakeResponse(payload={"status": "OK"})})
    with pytest.raises(AssertionError, match="unrouted request"):
        await coord._session.get("http://x/?cmd=somethingElse").__aenter__()
