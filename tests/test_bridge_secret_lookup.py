"""The bridge-secret lookup must pick the RUNNING add-on's secret, not any secret.

Measured on a real box 2026-10-16: nine `bridge_secret` files, eight belonging to
add-ons no longer installed (older repo-hash installs of the same add-on, two
Console dirs, four `chickadee` dirs). The shipped `addon_configs/dashie*` glob
matched NONE of them — HA names the dir after the add-on's full slug, which starts
with a repo hash. But widening it to `*dashie*` matched four and the code took
sorted()[0], a DEAD add-on's secret, and short-circuited before
`.dashie_voice/bridge_secret`, which held the right one.

So these legs pin ORDER and AUTHORITY, not just "a secret was found".
"""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.dashie import addon_bridge

RUNNING = "e55d11ca_dashie_ha_dev"      # the slug Supervisor reports
STALE = "01f10a62_dashie"               # sorts FIRST, belongs to a dead add-on


@pytest.fixture(autouse=True)
def _reset_module_state():
    addon_bridge._bridge_secret = None
    addon_bridge._discovered_slugs = []
    addon_bridge._glob_secret_warned = False
    yield
    addon_bridge._bridge_secret = None
    addon_bridge._discovered_slugs = []
    addon_bridge._glob_secret_warned = False


def _hass(tmp_path):
    hass = Mock()
    hass.config.path = lambda *a: str(tmp_path.joinpath(*a))

    async def _exec(func, *args):
        return func(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=_exec)
    return hass


def _write(tmp_path, rel, value):
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(value)
    return f


async def _headers(hass, slugs):
    with patch.object(addon_bridge, "_addon_slugs", AsyncMock(return_value=slugs)), \
         patch.object(addon_bridge, "async_get_clientsession", Mock(return_value=Mock())):
        return await addon_bridge._bridge_headers(hass)


@pytest.mark.asyncio
async def test_prefers_the_running_addons_secret_over_a_stale_one(tmp_path) -> None:
    """🔴 The leg that fails on the broadened glob: STALE sorts first alphabetically."""
    _write(tmp_path, f"addon_configs/{STALE}/bridge_secret", "stale-secret")
    _write(tmp_path, f"addon_configs/{RUNNING}/bridge_secret", "running-secret")
    h = await _headers(_hass(tmp_path), [RUNNING])
    assert "running-secret" in h.values()
    assert "stale-secret" not in h.values()


@pytest.mark.asyncio
async def test_falls_back_to_the_ha_config_channel_before_any_glob(tmp_path) -> None:
    """The real box's saving grace: .dashie_voice held the correct secret while every
    addon_configs dir the glob could see belonged to something dead."""
    _write(tmp_path, f"addon_configs/{STALE}/bridge_secret", "stale-secret")
    _write(tmp_path, ".dashie_voice/bridge_secret", "correct-secret")
    h = await _headers(_hass(tmp_path), [RUNNING])  # RUNNING has no file
    assert "correct-secret" in h.values()
    assert "stale-secret" not in h.values()


@pytest.mark.asyncio
async def test_glob_is_a_last_resort_and_is_not_cached(tmp_path) -> None:
    """A glob hit may be a dead add-on's secret, so it must not be pinned for the
    process — otherwise the authoritative path can never win later."""
    _write(tmp_path, f"addon_configs/{STALE}/bridge_secret", "stale-secret")
    hass = _hass(tmp_path)
    h = await _headers(hass, [])          # no Supervisor slugs at all
    assert "stale-secret" in h.values()   # used rather than sending no header
    assert addon_bridge._bridge_secret is None, "a glob hit must NOT be cached"
    # now the authoritative file appears, as it would when the add-on provisions
    _write(tmp_path, f"addon_configs/{RUNNING}/bridge_secret", "running-secret")
    h2 = await _headers(hass, [RUNNING])
    assert "running-secret" in h2.values()


@pytest.mark.asyncio
async def test_an_empty_secret_file_is_skipped(tmp_path) -> None:
    _write(tmp_path, f"addon_configs/{RUNNING}/bridge_secret", "   \n")
    _write(tmp_path, ".dashie_voice/bridge_secret", "real")
    h = await _headers(_hass(tmp_path), [RUNNING])
    assert "real" in h.values()


@pytest.mark.asyncio
async def test_no_secret_anywhere_returns_no_headers(tmp_path) -> None:
    assert await _headers(_hass(tmp_path), [RUNNING]) == {}


@pytest.mark.asyncio
async def test_a_miss_is_not_cached_so_the_startup_race_self_heals(tmp_path) -> None:
    """🔴 The property `authorize_device`'s retry depends on. If a miss were cached,
    the add-on finishing its start would never be noticed."""
    hass = _hass(tmp_path)
    assert await _headers(hass, [RUNNING]) == {}
    assert addon_bridge._bridge_secret is None
    _write(tmp_path, f"addon_configs/{RUNNING}/bridge_secret", "arrived-late")
    h = await _headers(hass, [RUNNING])
    assert "arrived-late" in h.values()


@pytest.mark.asyncio
async def test_a_success_IS_cached(tmp_path) -> None:
    hass = _hass(tmp_path)
    f = _write(tmp_path, f"addon_configs/{RUNNING}/bridge_secret", "s1")
    await _headers(hass, [RUNNING])
    assert addon_bridge._bridge_secret == "s1"
    f.write_text("s2")  # changed on disk; the cache should win
    h = await _headers(hass, [RUNNING])
    assert "s1" in h.values()
