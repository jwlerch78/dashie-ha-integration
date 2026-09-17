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
    yield aioclient_mock
    frigate_proxy._frigate_url = None
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
