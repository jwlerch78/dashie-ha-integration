"""The downloadable diagnostics file must never contain the device API password.

🔴 WHY THIS FILE EXISTS. The device API password is a QUERY PARAMETER, and
`_fetch_device_log` calls `response.raise_for_status()`. That makes
`aiohttp.ClientResponseError` reachable, and its `str()` embeds the full request URL.
The function's return value is rendered into the file a user downloads from
Settings -> Devices & services -> Dashie -> Download diagnostics — the file we ask
reporters to attach to GitHub issues. So before 2026-10-18 a device answering any
status >= 400 put the tablet's password into an artifact built to be shared.

⚠️ The trigger is correlated with the reason the file gets generated: the device answers
401 exactly when the stored password is wrong or stale, so the household most likely to
publish its own password is the one debugging why Dashie went unavailable.

⚠️ And `TO_REDACT` does not cover it — that scrubs `entry.data`, while this value leaves
through an exception string. A redaction list is not a boundary.

HOW THESE LEGS ARE BUILT, and it matters. The exception is raised by aiohttp's OWN
`raise_for_status()` from a real `RequestInfo`, via FakeResponse. It is NOT hand
constructed with a message I wrote — doing exactly that produced a false "leak" finding
earlier the same day (I had certified a leak with a string I planted), so the control
below asserts that aiohttp's real exception string DOES carry the password. Without that
control, every assertion here would pass just as well if the leak had never been possible.
"""
from __future__ import annotations

import asyncio

import aiohttp
import pytest

from custom_components.dashie.diagnostics import _fetch_device_log, _safe_err

from .fake_http import FakeResponse, FakeSession

SECRET = "MyD3vicePassw0rd!"
BASE = "http://192.168.1.77:2323"
LEAKY_URL = f"{BASE}/?cmd=getDiagnosticsLog&password={SECRET}"


class _Coordinator:
    """`_fetch_device_log` touches exactly three attributes."""

    def __init__(self, outcome) -> None:
        self.password = SECRET
        self.base_url = BASE
        self._session = FakeSession({"cmd=getDiagnosticsLog": outcome})

    async def _get_session(self):
        return self._session


# ── the control: the leak has to be real, or nothing below means anything ──────────────


def test_aiohttps_own_exception_really_does_carry_the_password() -> None:
    """POSITIVE CONTROL. If this ever fails, the legs below are vacuous, not safe.

    This is the pre-fix behavior, asserted rather than assumed: aiohttp's real
    ClientResponseError, raised by its real raise_for_status(), stringifies to something
    containing the password. The fix is only worth anything because this is true.
    """
    resp = FakeResponse(status=500, url=LEAKY_URL)
    with pytest.raises(aiohttp.ClientResponseError) as err:
        resp.raise_for_status()

    assert SECRET in str(err.value), (
        "aiohttp no longer puts the URL in ClientResponseError.__str__. The disclosure "
        "this file guards may be gone — re-derive before trusting the legs below."
    )


# ── the boundary ──────────────────────────────────────────────────────────────────────


async def test_an_http_error_does_not_put_the_password_in_the_payload() -> None:
    """The headline. A 500 from the device must not publish the tablet's password.

    Injection: restoring `f"...{err}"` reds this.
    """
    lines = await _fetch_device_log(_Coordinator(FakeResponse(status=500, url=LEAKY_URL)))

    blob = "\n".join(lines)
    assert SECRET not in blob, f"the diagnostics payload leaked the password: {blob}"
    assert "password" not in blob.lower(), f"payload mentions a password at all: {blob}"


async def test_the_401_case_specifically(hass) -> None:
    """401 is the case that matters most: it IS the wrong-password case.

    A stale password makes every poll 401, which is precisely when someone downloads
    diagnostics — so this is the leg that matches the real household, not just the
    generic >=400 path.
    """
    lines = await _fetch_device_log(_Coordinator(FakeResponse(status=401, url=LEAKY_URL)))
    assert SECRET not in "\n".join(lines)


async def test_the_payload_still_says_what_went_wrong() -> None:
    """Fail-safe must not mean useless — otherwise the next person re-adds `{err}`.

    Injection: returning a bare "(error)" reds this, which is the point: the reason this
    fix is stable is that it keeps the diagnostic value, so nobody has a motive to undo it.
    """
    lines = await _fetch_device_log(_Coordinator(FakeResponse(status=401, url=LEAKY_URL)))

    blob = "\n".join(lines)
    assert "401" in blob, f"status must survive so 401 is distinguishable from 500: {blob}"
    assert "ClientResponseError" in blob, f"exception type must survive: {blob}"


async def test_a_transport_failure_does_not_leak_either() -> None:
    """The other branch. ClientConnectorError's message holds host:port, not the query —
    but this asserts the OUTPUT, not that one exception type happens to be safe.
    """
    lines = await _fetch_device_log(_Coordinator(aiohttp.ClientPayloadError(LEAKY_URL)))
    assert SECRET not in "\n".join(lines)


async def test_an_unexpected_exception_type_does_not_leak(caplog) -> None:
    """The defensive branch, which also used to write the traceback to the LOG.

    `_LOGGER.exception` put the exception message - and so the URL - into
    home-assistant.log at ERROR, which HA shows by default. Same credential, second
    channel, and v1.6.3 already had to fix one of those.

    🔴 THE SUBJECT MUST NOT BE A ClientError, AND A SURVIVING MUTANT IS WHAT TAUGHT ME
    THAT. This leg first used `aiohttp.InvalidURL`, which looks perfect - its str() IS
    the raw url. But `InvalidURL` subclasses `ClientError`, so it is caught by the
    handler ABOVE and never reaches the defensive branch this leg claims to cover. The
    assertions passed, and they were measuring the wrong handler. Restoring
    `_LOGGER.exception` as a mutant did not red anything, which is the only reason it
    was found. `RuntimeError` is deliberately NOT a ClientError so the generic branch
    is genuinely the one under test.

    Injection: restoring _LOGGER.exception reds the caplog assertion.
    """
    lines = await _fetch_device_log(_Coordinator(RuntimeError(LEAKY_URL)))

    assert SECRET not in "\n".join(lines), "payload leaked"
    assert SECRET not in caplog.text, "the LOG leaked even though the payload did not"
    assert "RuntimeError" in "\n".join(lines), (
        "the generic branch was not the one exercised - a ClientError subclass is caught "
        "above, which is exactly how this leg was wrong before"
    )


async def test_a_timeout_says_so(hass) -> None:
    """Unchanged behavior, pinned so the refactor did not silently reclassify it."""
    lines = await _fetch_device_log(_Coordinator(asyncio.TimeoutError()))
    assert lines == ["(timeout fetching diagnostics from device)"]


# ── the helper itself ─────────────────────────────────────────────────────────────────


def test_safe_err_never_includes_the_message() -> None:
    """Fail-safe BY CONSTRUCTION: it must not interpolate str(err) at all.

    A redactor has to recognise a URL to scrub it and leaks whenever it fails to — which
    is how three separate `_redact_url` defects shipped. This takes the opposite shape, so
    no future exception type can leak through it. Injection: making _safe_err fall back to
    str(err) for unknown types reds this.
    """
    class _Exotic(Exception):
        def __str__(self) -> str:
            return LEAKY_URL

    assert _safe_err(_Exotic()) == "_Exotic"
    assert SECRET not in _safe_err(_Exotic())
