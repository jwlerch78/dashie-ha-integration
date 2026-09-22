"""Frigate URL detection: the official Frigate integration's URL is tried first.

The proxy used to probe only a fixed list of add-on hostnames, so a Frigate running
elsewhere (a NAS, another host) could not be reached without editing the file. Anyone
running Frigate with Home Assistant has normally set up the official Frigate integration,
whose config entry already holds that URL under `url`. These tests pin the order: the
entry's URL first, then the built-in candidates exactly as before.

The unreachable-entry test asserts the fallback is LOUD: an entry URL that does not
answer (typically Frigate's authenticated port) must log why, not vanish silently.
"""
import logging

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dashie import frigate_proxy

CANDIDATE = "http://frigate-candidate:5000"
EXTERNAL = "http://nas.lan:5000"


@pytest.fixture
async def frigate(hass, aioclient_mock, monkeypatch):
    """Point the proxy's session at the mocker and reset its cached URL."""
    session = aioclient_mock.create_session(hass.loop)

    async def _session():
        return session

    monkeypatch.setattr(frigate_proxy, "_get_session", _session)
    monkeypatch.setattr(frigate_proxy, "_FRIGATE_CANDIDATES", [CANDIDATE])
    monkeypatch.setattr(frigate_proxy, "_frigate_url", None)
    monkeypatch.setattr(frigate_proxy, "_not_found_warned", False)
    yield aioclient_mock
    frigate_proxy._frigate_url = None
    frigate_proxy._not_found_warned = False
    await session.close()


def _add_frigate_entry(hass, url):
    MockConfigEntry(domain="frigate", data={"url": url}).add_to_hass(hass)


async def test_no_frigate_entry_uses_the_candidates_as_before(hass, frigate):
    frigate.get(f"{CANDIDATE}/api/version", text="0.14.1")

    assert await frigate_proxy._detect_frigate() == CANDIDATE


async def test_frigate_entry_url_is_used_first(hass, frigate):
    _add_frigate_entry(hass, EXTERNAL)
    frigate.get(f"{EXTERNAL}/api/version", text="0.14.1")
    frigate.get(f"{CANDIDATE}/api/version", text="0.14.1")

    assert await frigate_proxy._detect_frigate() == EXTERNAL


async def test_trailing_slash_on_the_entry_url_is_dropped(hass, frigate):
    _add_frigate_entry(hass, EXTERNAL + "/")
    frigate.get(f"{EXTERNAL}/api/version", text="0.14.1")

    assert await frigate_proxy._detect_frigate() == EXTERNAL


async def test_unreachable_entry_falls_back_and_says_why(hass, frigate, caplog):
    authed = "http://nas.lan:8971"
    _add_frigate_entry(hass, authed)
    frigate.get(f"{authed}/api/version", status=401)
    frigate.get(f"{CANDIDATE}/api/version", text="0.14.1")

    with caplog.at_level(logging.WARNING, logger=frigate_proxy.__name__):
        assert await frigate_proxy._detect_frigate() == CANDIDATE

    drops = [r.getMessage() for r in caplog.records if "DROP:" in r.getMessage()]
    assert drops, "an unusable Frigate integration URL must be logged, not skipped silently"
    assert authed in drops[0] and "401" in drops[0] and "5000" in drops[0]


# ── D-101: a household with no Frigate pays for the feature every 30 seconds ──
#
# chicknlil (dashie-ha-integration #3 thread, 09-20): "Can we just disable Frigate
# period? I'm using Dashie like FK, just displaying a local HA dashboard. I keep
# getting this logger error." Two independent causes, both here:
#
#   1. `_detect_frigate` re-probes on every miss and logs at WARNING every time.
#   2. feed_registry caches a FOUND Frigate for 300s but an ABSENT one for only 30s,
#      so absence is re-probed 10x more often than presence.
#
# The 30s was written for a Frigate that is temporarily down ("short enough to
# self-heal") — it optimises for the rare case and bills the common one.


async def test_repeated_misses_warn_once_not_every_probe(hass, frigate, caplog):
    """A box that will never have Frigate must not warn on every probe, forever."""
    frigate.get(f"{CANDIDATE}/api/version", exc=OSError("no route to host"))

    with caplog.at_level(logging.DEBUG, logger=frigate_proxy.__name__):
        assert await frigate_proxy._detect_frigate() is None
        assert await frigate_proxy._detect_frigate() is None
        assert await frigate_proxy._detect_frigate() is None

    warned = [
        r.getMessage() for r in caplog.records
        if r.levelno >= logging.WARNING and "not found at any candidate" in r.getMessage()
    ]
    assert len(warned) == 1, (
        "the 'Frigate not found' line must be loud ONCE and quiet after that; "
        f"got {len(warned)} WARNING-level records across 3 probes"
    )


async def test_absence_is_not_cached_more_briefly_than_presence(hass, frigate, monkeypatch):
    """The empty-result TTL must not be shorter than the success TTL.

    This is the asymmetry itself, pinned as behaviour rather than as a constant:
    60s after an empty probe (past the old 30s, inside the 300s success TTL) the
    cached empty list must still be served, i.e. no second detection round-trip.
    """
    from custom_components.dashie import feed_registry

    monkeypatch.setattr(feed_registry, "_frigate_camera_cache", None)
    monkeypatch.setattr(feed_registry, "_frigate_cache_time", 0.0)
    monkeypatch.setattr(feed_registry, "_frigate_ever_found", False)
    frigate.get(f"{CANDIDATE}/api/version", exc=OSError("no route to host"))

    probes = {"n": 0}
    real_detect = frigate_proxy._detect_frigate

    async def counting_detect():
        probes["n"] += 1
        return await real_detect()

    monkeypatch.setattr(frigate_proxy, "_detect_frigate", counting_detect)

    now = {"t": 1_000_000.0}
    monkeypatch.setattr(feed_registry.time, "time", lambda: now["t"])

    assert await feed_registry._get_frigate_camera_names() == []
    now["t"] += 60  # past the old 30s empty-TTL, well inside the 300s success TTL
    assert await feed_registry._get_frigate_camera_names() == []

    assert probes["n"] == 1, (
        "absence was re-probed within 60s while presence is cached for 300s — "
        f"the empty TTL is still shorter than the success TTL ({probes['n']} probes)"
    )


async def test_a_frigate_that_breaks_still_self_heals_despite_the_longer_ttl(hass, frigate, monkeypatch):
    """The regression D-101's fix could plausibly cause, pinned.

    Raising the empty-result TTL from 30s to 300s is only safe because a Frigate
    that WAS working and then fails clears `frigate_proxy._frigate_url` on the
    error path, forcing a full re-probe on the next call regardless of this TTL.
    That reasoning was load-bearing and untested, so: break a working Frigate and
    assert the very next call re-probes rather than serving a stale empty list for
    five minutes.
    """
    from custom_components.dashie import feed_registry

    monkeypatch.setattr(feed_registry, "_frigate_camera_cache", None)
    monkeypatch.setattr(feed_registry, "_frigate_cache_time", 0.0)
    monkeypatch.setattr(feed_registry, "_frigate_ever_found", False)

    now = {"t": 2_000_000.0}
    monkeypatch.setattr(feed_registry.time, "time", lambda: now["t"])

    # 1. Frigate is up and answers /api/config — cameras are cached for the long TTL.
    frigate.get(f"{CANDIDATE}/api/version", text="0.14.1")
    frigate.get(f"{CANDIDATE}/api/config", json={"cameras": {"pool": {}, "porch": {}}})
    assert await feed_registry._get_frigate_camera_names() == ["pool", "porch"]
    assert frigate_proxy._frigate_url == CANDIDATE

    # 2. Frigate breaks. Past the long TTL so the cache is consulted and re-fetched.
    frigate.clear_requests()
    frigate.get(f"{CANDIDATE}/api/version", exc=OSError("container gone"))
    frigate.get(f"{CANDIDATE}/api/config", exc=OSError("container gone"))
    now["t"] += _long_ttl(feed_registry) + 1
    assert await feed_registry._get_frigate_camera_names() == []

    # The failed fetch must have invalidated the cached URL — that is the self-heal.
    assert frigate_proxy._frigate_url is None, (
        "a failed camera fetch must clear the cached Frigate URL so the next call "
        "re-probes; without this the longer empty-TTL would strand a recovered Frigate"
    )

    # 3. Frigate comes back. It must be picked up on the SHORT (lost) TTL, which is
    #    what the pre-D-101 code gave, and NOT be stranded for the long absent TTL.
    frigate.clear_requests()
    frigate.get(f"{CANDIDATE}/api/version", text="0.14.1")
    frigate.get(f"{CANDIDATE}/api/config", json={"cameras": {"pool": {}}})

    now["t"] += feed_registry._FRIGATE_LOST_TTL - 1  # still inside the lost TTL
    assert await feed_registry._get_frigate_camera_names() == [], (
        "inside the lost TTL the cached empty list is still the right answer"
    )

    now["t"] += 2  # now past it
    assert await feed_registry._get_frigate_camera_names() == ["pool"], (
        "a Frigate that was working, broke and recovered must come back on the SHORT "
        "lost TTL, not be stranded for the long never-found TTL"
    )

    assert feed_registry._FRIGATE_LOST_TTL < feed_registry._FRIGATE_ABSENT_TTL, (
        "the whole point of D-101 is that these two cases are NOT the same case"
    )


def _long_ttl(feed_registry):
    return feed_registry._FRIGATE_CACHE_TTL
