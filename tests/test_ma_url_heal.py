"""D-242 — a stale central Music Assistant URL must not be served, and must not be dropped silently.

The reported bug: a household's HA box changed IP, the stored `ma_url` kept pointing at the old
host, and the user could not fix it. The value lives HERE, not on the tablet, so reinstalling the
app pulled it straight back; and it is only replaced on a SUCCESSFUL Music Assistant login, which
the stale URL is precisely what breaks. The reporter corrected it by hand repeatedly and every
correction was discarded.

Two halves are pinned here:

1. **GET withholds a stored `ma_url` that does not answer**, keeping the token, so the device falls
   through to deriving an address from the HA host it is already talking to. HA is the right
   observer for this — always on, always on the home LAN, and it holds the value — and it is the
   only route that reaches tablets which will never be updated.
2. **`set_config` no longer swallows a failed `ma_url` write.** Both of its failure paths were log
   lines, so the service reported success and did nothing. That matters because an `ma_url`-only
   call is the one workaround we hand an affected household: a silently-dropped repair looks to the
   user like one more thing that did not work.

⚠️ Each assertion below was shown able to fail; the injection is named per test.

---

🔴 **PORTED OFF `aioresponses` 2026-10-17, AND THE PORT IS THE POINT.** This file used to patch
aiohttp's internals via `aioresponses`, which stopped working when aiohttp changed them:
`ClientResponse.__init__() missing 1 required keyword-only argument: 'stream_writer'`. 0.7.9 is the
newest release there is, so there was nothing to upgrade to. It now uses `tests/fake_http.py`,
injected at our own seam, which is version-proof by construction because we own the surface.

⚠️ **What the old version cost is the part worth remembering, because three RED tests were the
cheap half.** On HA 2026.9.3 the `integration` fixture's mock served NOTHING — measured on both
lanes side by side: `last_update_success=False` and `data=None` on current, `True` and the real
payload on floor. Two tests using that fixture **passed anyway**, because their assertions only
ever touched the token store, never the device data. So the broken instrument produced three
honest reds and two green NON-ANSWERS, and only the reds were visible.

⇒ **Every leg below asserts on `fake.requests`.** An injection that silently misses now reds the
test instead of passing quietly. That assertion is not ceremony — it is the only thing that
distinguishes "the code did the right thing" from "the code was never reached".
"""
import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dashie import music_token_store
from custom_components.dashie.music_token_store import (
    DashieMusicTokenView,
    MusicTokenStore,
    async_ma_url_answers,
)

from .fake_http import FakeResponse, FakeSession
from .fake_http_wiring import patch_sessions

DOMAIN = "dashie"
IPV4 = "192.168.23.96"
BASE = f"http://{IPV4}:2323"

LIVE_MA = "http://192.168.1.250:8095"
DEAD_MA = "http://192.168.1.248:8095"
TOKEN = "ma-jwt-value"


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """The probe cache is module-global; a leak between tests would hide a real re-probe."""
    music_token_store._probe_cache.clear()
    yield
    music_token_store._probe_cache.clear()


@contextmanager
def probe_session(fake: FakeSession):
    """Route the PROBE's session at `fake`.

    `async_ma_url_answers` asks HA for the shared session (music_token_store.py:77), so the
    seam is that module's own imported name. `patch_sessions` does this too, among four other
    modules — this narrower helper is for the legs that need no coordinator at all.

    create=False deliberately: if the import ever moves, the patch must FAIL rather than
    quietly cover nothing.
    """
    with patch("custom_components.dashie.music_token_store.async_get_clientsession",
               return_value=fake, create=False):
        yield fake


class _Request:
    """The views take an aiohttp request and use only `app["hass"]`."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.app = {"hass": hass}


async def _store_with(hass: HomeAssistant, token: str, ma_url: str) -> MusicTokenStore:
    store = MusicTokenStore(hass)
    store._data = {"token": token, "ma_url": ma_url}
    hass.data.setdefault(DOMAIN, {})["music_token_store"] = store
    return store


async def _json(response) -> dict:
    import json

    return json.loads(response.body.decode())


# ── the probe itself ────────────────────────────────────────────────────────────────────────


async def test_a_host_that_answers_counts_as_reachable(hass: HomeAssistant) -> None:
    """Any status counts — MA answers 401 for a stale token and is still reachable.

    Injection: treating only 2xx as reachable reds the 401 case.
    """
    fake = FakeSession({LIVE_MA: FakeResponse(status=200)})
    with probe_session(fake):
        assert await async_ma_url_answers(hass, LIVE_MA) is True
    assert fake.requests, "the probe never reached the fake — this leg measured nothing"

    music_token_store._probe_cache.clear()
    fake = FakeSession({LIVE_MA: FakeResponse(status=401)})
    with probe_session(fake):
        assert await async_ma_url_answers(hass, LIVE_MA) is True
    assert fake.requests


async def test_a_host_that_does_not_answer_counts_as_absent(hass: HomeAssistant) -> None:
    """A connection that never completes is the stale-host case.

    Injection: dropping OSError from the except clause makes this raise instead of returning False.
    """
    fake = FakeSession({DEAD_MA: asyncio.TimeoutError()})
    with probe_session(fake):
        assert await async_ma_url_answers(hass, DEAD_MA) is False
    assert fake.requests

    music_token_store._probe_cache.clear()
    fake = FakeSession({DEAD_MA: OSError("connection refused")})
    with probe_session(fake):
        assert await async_ma_url_answers(hass, DEAD_MA) is False
    assert fake.requests


async def test_a_host_that_hangs_is_cut_off_by_the_real_timeout(hass: HomeAssistant) -> None:
    """NEW with the port, and it covers what `aioresponses` never did.

    The old version routed `exception=asyncio.TimeoutError()` — which RAISES the exception the
    timeout would have raised, and so never executed `asyncio.timeout` at all. The stale-host
    case in the field is a host that ACCEPTS and then hangs, which is the wrapper's job.

    Here the session genuinely sleeps past the budget, so `asyncio.timeout` is the thing that
    ends the call. The budget is shortened to keep the suite fast; the sleep is 20x it.

    Injection: removing the `async with asyncio.timeout(...)` wrapper hangs this test instead
    of passing it — which is a failure, and loudly so.
    """
    class _Hangs(FakeSession):
        def get(self, url, params=None, **kwargs):
            self._record("GET", str(url), kwargs)

            class _Ctx:
                async def __aenter__(_self):
                    await asyncio.sleep(1.0)
                    raise AssertionError("the timeout did not fire")

                async def __aexit__(_self, *exc):
                    return False

            return _Ctx()

    fake = _Hangs(default=FakeResponse(status=200))
    with probe_session(fake), patch.object(music_token_store, "PROBE_TIMEOUT_SEC", 0.05):
        assert await async_ma_url_answers(hass, DEAD_MA) is False
    assert fake.requests, "the probe never reached the fake — this leg measured nothing"


async def test_the_probe_result_is_cached(hass: HomeAssistant) -> None:
    """Two calls, one request: the second must not reach the network.

    Without the cache every auth failure re-probes. The old version relied on `aioresponses`
    raising for an unmatched request; the fake's own URL log is a direct count instead, which
    says what happened rather than depending on a library's error behavior.

    Injection: removing the cache lookup makes this 2 and reds the assertion.
    """
    fake = FakeSession({LIVE_MA: FakeResponse(status=200)})
    with probe_session(fake):
        assert await async_ma_url_answers(hass, LIVE_MA) is True
        assert await async_ma_url_answers(hass, LIVE_MA) is True

    assert len(fake.requests) == 1, f"the second call re-probed: {fake.requests}"


# ── the GET view ───────────────────────────────────────────────────────────────────────────


async def test_get_withholds_an_unreachable_url_but_keeps_the_token(hass: HomeAssistant) -> None:
    """The repair: the device is told to derive its own address, and is NOT logged out.

    Keeping the token is half the requirement — clearing it would force the household through a
    fresh Music Assistant sign-in, which is the thing the stale URL was already blocking.

    Injection: returning `store.get_token()` unconditionally reds the ma_url assertion; calling the
    DELETE path instead of withholding reds the token assertion.
    """
    await _store_with(hass, TOKEN, DEAD_MA)
    fake = FakeSession({DEAD_MA: asyncio.TimeoutError()})
    with probe_session(fake):
        payload = await _json(await DashieMusicTokenView().get(_Request(hass)))

    assert fake.requests, "the view never probed — the withhold was not exercised"
    assert payload["ma_url"] == "", "a url nothing answers must not be handed to a device"
    assert payload["token"] == TOKEN, "the token must survive a withheld url"


async def test_get_passes_a_reachable_url_through_unchanged(hass: HomeAssistant) -> None:
    """The control that protects working households.

    Without this leg the fix could withhold every url, including correct ones, and every device
    would fall back to derivation — which is wrong for any household running MA on a separate box.

    Injection: withholding unconditionally reds this.
    """
    await _store_with(hass, TOKEN, LIVE_MA)
    fake = FakeSession({LIVE_MA: FakeResponse(status=200)})
    with probe_session(fake):
        payload = await _json(await DashieMusicTokenView().get(_Request(hass)))

    assert fake.requests, "the view never probed — a pass-through that probed nothing proves nothing"
    assert payload["ma_url"] == LIVE_MA
    assert payload["token"] == TOKEN


async def test_delete_clears_the_stored_value(hass: HomeAssistant) -> None:
    """The DELETE route had no caller anywhere, so this settles whether it works.

    It is the "clear the poisoned value" operation the deadlock lacked. It drops the token as well
    as the url, which is why it stays manual and why the probe path withholds instead of deleting.

    No session is routed here on purpose: DELETE must not probe. A FakeSession with no routes
    and no default raises on any request, so a DELETE that grew a probe would red this.

    Injection: returning without saving reds the second assertion.
    """
    store = await _store_with(hass, TOKEN, DEAD_MA)
    fake = FakeSession()
    with probe_session(fake):
        payload = await _json(await DashieMusicTokenView().delete(_Request(hass)))

    assert fake.requests == [], "DELETE must not probe the network"
    assert payload == {"cleared": True}
    assert store.get_token() == {}, "a cleared store must serve nothing"


# ── set_config no longer swallows a failed write ───────────────────────────────────────────


@pytest.fixture
async def integration(hass: HomeAssistant):
    """Boot the component so its services exist, with platforms and the device stubbed.

    🔴 This fixture is the one that was silently dead on HA 2026.9.3 (see the module docstring).
    It now asserts its own injection worked, so it can never again boot a coordinator that
    reached nothing while the tests built on it report green.
    """
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="dev1", title="Kitchen Tablet",
        data={"host": IPV4, "port": 2323, "device_id": "dev1"},
    )
    entry.add_to_hass(hass)
    hass.set_state(CoreState.running)
    fake = FakeSession({
        "cmd=deviceInfo": FakeResponse(payload={
            "deviceID": "dev1", "stableDeviceID": "dev1", "deviceName": "Kitchen",
        }),
        "cmd=getRtspStatus": asyncio.TimeoutError(),
        "cmd=getRtspConfig": asyncio.TimeoutError(),
    })
    with patch_sessions(fake), patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator.last_update_success, (
            "the fixture's own HTTP injection missed — every test built on it would be a "
            "non-answer. This is the assertion the aioresponses version did not have."
        )
        assert coordinator.data["deviceID"] == "dev1", "the coordinator served someone else's data"
        yield entry


async def test_ma_url_without_a_stored_token_fails_visibly(hass: HomeAssistant, integration) -> None:
    """The silent drop, closed.

    This was a `_LOGGER.warning(... "skipping")`, so the service returned normally and wrote
    nothing — indistinguishable from success to a user following a support instruction.

    Injection: restoring the warning-and-continue path reds `pytest.raises`.
    """
    await _store_with(hass, "", "")

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN, "set_config", {"ma_url": LIVE_MA}, blocking=True
        )

    assert "ma_url" in str(err.value), "the error must name what failed, not just fail"
    store = hass.data[DOMAIN]["music_token_store"]
    assert store.get_token() == {}, "a rejected call must not half-write"


async def test_ma_url_alone_updates_the_url_and_keeps_the_token(
    hass: HomeAssistant, integration
) -> None:
    """The workaround itself: with a token already held, an ma_url-only call repairs the household.

    This is the path a support reply points at, so it has to actually work.

    Injection: dropping the `existing` lookup so the token is not carried forward reds the token
    assertion.
    """
    await _store_with(hass, TOKEN, DEAD_MA)

    await hass.services.async_call(DOMAIN, "set_config", {"ma_url": LIVE_MA}, blocking=True)

    store = hass.data[DOMAIN]["music_token_store"]
    assert store._data["ma_url"] == LIVE_MA
    assert store._data["token"] == TOKEN, "updating the url must preserve the token"
