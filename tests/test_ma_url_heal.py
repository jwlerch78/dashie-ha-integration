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

The probe is exercised for real through `aioresponses` rather than monkeypatched, so the timeout
and exception handling in `async_ma_url_answers` are actually covered.

⚠️ Each assertion below was shown able to fail; the injection is named per test.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from aioresponses import aioresponses
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dashie import music_token_store
from custom_components.dashie.music_token_store import (
    DashieMusicTokenView,
    MusicTokenStore,
    async_ma_url_answers,
)

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
    with aioresponses() as mock:
        mock.get(f"{LIVE_MA}/", status=200)
        assert await async_ma_url_answers(hass, LIVE_MA) is True

    music_token_store._probe_cache.clear()
    with aioresponses() as mock:
        mock.get(f"{LIVE_MA}/", status=401)
        assert await async_ma_url_answers(hass, LIVE_MA) is True


async def test_a_host_that_does_not_answer_counts_as_absent(hass: HomeAssistant) -> None:
    """A connection that never completes is the stale-host case.

    Injection: dropping OSError from the except clause makes this raise instead of returning False.
    """
    with aioresponses() as mock:
        mock.get(f"{DEAD_MA}/", exception=asyncio.TimeoutError())
        assert await async_ma_url_answers(hass, DEAD_MA) is False

    music_token_store._probe_cache.clear()
    with aioresponses() as mock:
        mock.get(f"{DEAD_MA}/", exception=OSError("connection refused"))
        assert await async_ma_url_answers(hass, DEAD_MA) is False


async def test_the_probe_result_is_cached(hass: HomeAssistant) -> None:
    """One mocked response, two calls: the second must not reach the network.

    Without the cache every auth failure re-probes. aioresponses raises on an unmatched request,
    so a second network call fails this test rather than passing quietly.

    Injection: removing the cache lookup reds this.
    """
    with aioresponses() as mock:
        mock.get(f"{LIVE_MA}/", status=200)  # exactly one
        assert await async_ma_url_answers(hass, LIVE_MA) is True
        assert await async_ma_url_answers(hass, LIVE_MA) is True


# ── the GET view ───────────────────────────────────────────────────────────────────────────


async def test_get_withholds_an_unreachable_url_but_keeps_the_token(hass: HomeAssistant) -> None:
    """The repair: the device is told to derive its own address, and is NOT logged out.

    Keeping the token is half the requirement — clearing it would force the household through a
    fresh Music Assistant sign-in, which is the thing the stale URL was already blocking.

    Injection: returning `store.get_token()` unconditionally reds the ma_url assertion; calling the
    DELETE path instead of withholding reds the token assertion.
    """
    await _store_with(hass, TOKEN, DEAD_MA)
    with aioresponses() as mock:
        mock.get(f"{DEAD_MA}/", exception=asyncio.TimeoutError())
        payload = await _json(await DashieMusicTokenView().get(_Request(hass)))

    assert payload["ma_url"] == "", "a url nothing answers must not be handed to a device"
    assert payload["token"] == TOKEN, "the token must survive a withheld url"


async def test_get_passes_a_reachable_url_through_unchanged(hass: HomeAssistant) -> None:
    """The control that protects working households.

    Without this leg the fix could withhold every url, including correct ones, and every device
    would fall back to derivation — which is wrong for any household running MA on a separate box.

    Injection: withholding unconditionally reds this.
    """
    await _store_with(hass, TOKEN, LIVE_MA)
    with aioresponses() as mock:
        mock.get(f"{LIVE_MA}/", status=200)
        payload = await _json(await DashieMusicTokenView().get(_Request(hass)))

    assert payload["ma_url"] == LIVE_MA
    assert payload["token"] == TOKEN


async def test_delete_clears_the_stored_value(hass: HomeAssistant) -> None:
    """The DELETE route had no caller anywhere, so this settles whether it works.

    It is the "clear the poisoned value" operation the deadlock lacked. It drops the token as well
    as the url, which is why it stays manual and why the probe path withholds instead of deleting.

    Injection: returning without saving reds the second assertion.
    """
    store = await _store_with(hass, TOKEN, DEAD_MA)
    payload = await _json(await DashieMusicTokenView().delete(_Request(hass)))

    assert payload == {"cleared": True}
    assert store.get_token() == {}, "a cleared store must serve nothing"


# ── set_config no longer swallows a failed write ───────────────────────────────────────────


@pytest.fixture
async def integration(hass: HomeAssistant):
    """Boot the component so its services exist, with platforms and the device stubbed."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="dev1", title="Kitchen Tablet",
        data={"host": IPV4, "port": 2323, "device_id": "dev1"},
    )
    entry.add_to_hass(hass)
    hass.set_state(CoreState.running)
    with aioresponses() as mock, patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        mock.get(
            f"{BASE}/?cmd=deviceInfo&type=json",
            payload={"deviceID": "dev1", "stableDeviceID": "dev1", "deviceName": "Kitchen"},
            repeat=True,
        )
        mock.get(f"{BASE}/?cmd=getRtspStatus", exception=asyncio.TimeoutError(), repeat=True)
        mock.get(f"{BASE}/?cmd=getRtspConfig", exception=asyncio.TimeoutError(), repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


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
