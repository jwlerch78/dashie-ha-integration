"""A kiosk authorize that lands during the add-on's startup must retry, not fail.

The transient, measured on a real box 2026-10-16: HA and the integration finished
starting before the Dashie add-on. The bridge-secret lookup missed exactly once and
then succeeded (it never caches a miss), but the kiosk authorize 3 seconds later
returned `addon_unavailable` and the tablet got a hard failure for a condition that
cleared itself seconds later.

⚠️ Only `addon_unavailable` may be retried. A 401, a 404 or a refusal from jwt-auth
is an ANSWER — retrying an answer just delays it, and on an auth path that would
also mean re-submitting a device code someone already rejected.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.dashie import addon_bridge

UNAVAILABLE = ({"error": "addon_unavailable", "message": "not yet"}, 503)
OK = ({"success": True, "account_email": "a@b.invalid"}, 200)


@pytest.fixture(autouse=True)
def _no_sleep():
    """The delay is real in production; here it would just make the suite slow."""
    with patch.object(addon_bridge.asyncio, "sleep", AsyncMock()):
        yield


async def _run(sequence):
    once = AsyncMock(side_effect=sequence)
    with patch.object(addon_bridge, "_authorize_device_once", once):
        body, status = await addon_bridge.authorize_device(object(), "CODE123")
    return body, status, once.await_count


@pytest.mark.asyncio
async def test_a_transient_unavailable_is_retried_and_succeeds() -> None:
    """🔴 The leg that fails if the retry is removed."""
    body, status, calls = await _run([UNAVAILABLE, OK])
    assert status == 200 and body.get("success") is True
    assert calls == 2


@pytest.mark.asyncio
async def test_it_recovers_on_the_last_permitted_attempt() -> None:
    body, status, calls = await _run([UNAVAILABLE, UNAVAILABLE, OK])
    assert status == 200
    assert calls == addon_bridge.AUTHORIZE_RETRIES + 1 == 3


@pytest.mark.asyncio
async def test_it_gives_up_bounded_rather_than_forever() -> None:
    body, status, calls = await _run([UNAVAILABLE] * 10)
    assert status == 503 and body["error"] == "addon_unavailable"
    assert calls == addon_bridge.AUTHORIZE_RETRIES + 1, "must be bounded"


@pytest.mark.asyncio
async def test_giving_up_is_loud(caplog) -> None:
    import logging
    caplog.set_level(logging.DEBUG, logger="custom_components.dashie.addon_bridge")
    await _run([UNAVAILABLE] * 10)
    drops = [r.getMessage() for r in caplog.records if "DROP:" in r.getMessage()]
    assert any("attempt 1 of" in m for m in drops), "must be loud on the FIRST failure"
    assert any("gave up" in m for m in drops), "must be loud when it gives up"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        ({"error": "unauthorized"}, 401),
        ({"error": "device_code_not_found"}, 404),
        ({"success": False, "error": "sharing_disabled"}, 403),
        ({"error": "addon_unavailable"}, 500),   # same error text, different status
        ({"error": "something_else"}, 503),      # same status, different error
    ],
)
async def test_an_answer_is_never_retried(answer) -> None:
    """Retrying a real refusal would delay it and re-submit a rejected code."""
    body, status, calls = await _run([answer, OK])
    assert calls == 1, f"retried a definitive answer: {answer}"
    assert (body, status) == answer


@pytest.mark.asyncio
async def test_success_first_time_does_not_retry() -> None:
    body, status, calls = await _run([OK])
    assert status == 200 and calls == 1


@pytest.mark.asyncio
async def test_the_cached_base_is_cleared_between_attempts() -> None:
    """A stale _working_base would make every retry fail identically, which would
    make the retry theatre rather than a fix."""
    addon_bridge._working_base = "http://dead-addon:8099"
    await _run([UNAVAILABLE, OK])
    assert addon_bridge._working_base is None
