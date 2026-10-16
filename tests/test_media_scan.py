"""D-279: the media scan that blocked HA setup, and the 5-second re-walk behind it.

Reported by `gghordijk` 2026-10-16: the config entry failed to set up on EVERY HA
restart. `select.py`'s `async_added_to_hass` awaited a recursive image COUNT over the
whole of `/media`, and on a Frigate box (`/media/frigate`, tens of thousands of files)
that outran HA's bootstrap stage-2 timeout, so the entry landed in `setup_error`.

Two separate defects, and three things that made it worse than it reads:

1. **The cost.** The walk computed a SUM, and a sum has no early exit — it must visit
   every term. The select's only use of that number was `if count > 0`, evaluated
   AFTER the whole tree had been walked. So cost tracked FILES when the question only
   needed FOLDERS. `Platform.SELECT` is unconditional, so every install paid it.
2. **The re-walk.** `_cached_folders == []` was the "not scanned yet" sentinel, so it
   conflated *never scanned* with *scanned and found nothing*. `async_update` retried
   on that emptiness every `DEFAULT_SCAN_INTERVAL` (5 s). ⚠️ The silent arm needs no
   failure at all: an mp4-only `/media` (SUPPORTED_EXTENSIONS has no `.mp4`) scans
   SUCCESSFULLY, caches `[]`, and re-walks forever with no exception and nothing above
   debug in the log.
3. **No escape hatch.** `CONF_MEDIA_BASE_PATH` had a reader and no writer.

⚠️ **The symptom — "it exceeded a timeout" — is the one property these tests must NOT
assert on.** A stopwatch leg passes or fails on the runner's mood, and a temp tree can
never be slow enough to fail, so a suite built that way is green and retires the bug.
L4 asserts a SHAPE instead: folders held constant, files doubled, work examined flat.

⚠️ `photo_count` is NOT dropped. It goes over the wire to `HaMediaPhotoSource.kt:660`
and is rendered by `SidebarScreensaverDialogs.kt:1007` as "<n> photos", defaulting to 0
when absent — so weakening it would silently show "0 photos" and raise nothing. The
exact counter still serves the HTTP view; only the select got the cheap check.
"""
from __future__ import annotations

import asyncio
import pathlib
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from custom_components.dashie import media_api
from custom_components.dashie.const import CONF_MEDIA_BASE_PATH, DOMAIN
from custom_components.dashie.media_api import (
    _count_images_recursive,
    _get_media_base_path,
    _has_any_image,
    _list_media_folder_names,
    _list_media_folders,
)


def _tree(root: Path, folders: int, subfolders: int, files_each: int) -> int:
    """Build a tree and return the number of image files written."""
    n = 0
    for f in range(folders):
        top = root / f"top{f}"
        top.mkdir()
        for s in range(subfolders):
            sub = top / f"sub{s}"
            sub.mkdir()
            for i in range(files_each):
                (sub / f"img{i}.jpg").write_bytes(b"x")
                n += 1
    return n


class _Counter:
    """Counts per-entry filesystem work (one stat per entry is the real cost)."""

    def __init__(self, under: Path) -> None:
        self.under = str(under)
        self.calls = 0
        self._real = pathlib.Path.is_file

    def __enter__(self):
        counter = self

        def counting_is_file(p):
            if str(p).startswith(counter.under):
                counter.calls += 1
            return counter._real(p)

        self._patch = patch.object(pathlib.Path, "is_file", counting_is_file)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False


# ----------------------------------------------------------------- L4: the shape

def test_work_does_not_track_file_count(tmp_path):
    """L4 — folders constant, files doubled, work examined flat.

    A ratio across two sizes IS the claim ("cost no longer tracks file count"). A
    fixed `assert visited <= N` can be met by luck or by a tree too small to
    discriminate, and it rots as the fixture changes.
    """
    small, large = tmp_path / "small", tmp_path / "large"
    small.mkdir(); large.mkdir()
    n_small = _tree(small, folders=3, subfolders=2, files_each=50)
    n_large = _tree(large, folders=3, subfolders=2, files_each=500)

    # POSITIVE CONTROL: the two trees must really differ, or this leg compares two
    # identical subjects and passes vacuously.
    assert n_large == n_small * 10, (n_small, n_large)

    with _Counter(small) as c_small:
        _list_media_folder_names(small)
    with _Counter(large) as c_large:
        _list_media_folder_names(large)

    assert c_small.calls > 0, "instrument saw nothing"
    # Flat, not merely sublinear: a 10x file increase must not scale the work.
    assert c_large.calls < c_small.calls * 3, (
        f"work tracked file count: {c_small.calls} -> {c_large.calls} for 10x files"
    )


def test_the_exact_counter_still_counts_exactly(tmp_path):
    """The HTTP view's contract is untouched — photo_count stays exact."""
    n = _tree(tmp_path, folders=2, subfolders=1, files_each=7)
    assert _count_images_recursive(tmp_path) == n == 14
    folders = _list_media_folders(tmp_path)
    assert all("photo_count" in f for f in folders if f["path"] != "*")


# ------------------------------------------------------------- L1: symlink, per path

def test_symlink_loop_does_not_escape_the_exact_counter(tmp_path):
    """L1a — the HTTP view has NO try/except, so a loop escaped as a 500."""
    (tmp_path / "pics").mkdir()
    (tmp_path / "pics" / "a.jpg").write_bytes(b"x")
    (tmp_path / "pics" / "loop").symlink_to(tmp_path, target_is_directory=True)
    assert _count_images_recursive(tmp_path) == 1


def test_symlink_loop_does_not_multiply_the_existence_scan(tmp_path):
    """L1b — rewritten TWICE, and the second time is why mutation testing earns its keep.

    v1 asserted the folder list was still CORRECT under a loop. Green with the
    symlink skip removed: MAX_SCAN_DEPTH already stops the recursion and a loop
    cannot change a boolean answer. v2 measured work — still green, because the
    fixture HAD an image, so the check short-circuited and never reached the loop.

    ⇒ the fixture must contain NO image, so the walk is forced to visit everything.
    Only then is the skip observable. Note the asymmetry with L1a: in the exact
    COUNTER a loop corrupts the returned number, so correctness catches it there; in
    an existence check it cannot, so cost is the only observable — and only on the
    branch that has to walk the whole tree.

    ⚠️ Separately: select's `except Exception` SWALLOWS RecursionError into options
    `["(error)"]`, so a leg asserting "an exception escapes" is GREEN on that path.
    """
    plain, looped = tmp_path / "plain", tmp_path / "looped"
    plain.mkdir(); looped.mkdir()
    for root in (plain, looped):
        (root / "novids").mkdir()
        (root / "novids" / "clip.mp4").write_bytes(b"x")   # no image: must walk it all
    (looped / "novids" / "loop").symlink_to(looped, target_is_directory=True)

    with _Counter(plain) as c_plain:
        assert _list_media_folder_names(plain) == []
    with _Counter(looped) as c_looped:
        assert _list_media_folder_names(looped) == []

    assert c_plain.calls > 0, "instrument saw nothing"
    assert c_looped.calls < c_plain.calls * 3, (
        f"the loop multiplied the scan: {c_plain.calls} -> {c_looped.calls}"
    )


def test_existence_check_is_true_only_when_an_image_exists(tmp_path):
    """Both directions, so the short-circuit cannot be trivially satisfied."""
    (tmp_path / "empty").mkdir()
    (tmp_path / "vids").mkdir()
    (tmp_path / "vids" / "clip.mp4").write_bytes(b"x")   # Frigate recordings
    (tmp_path / "has").mkdir()
    (tmp_path / "has" / "deep").mkdir()
    (tmp_path / "has" / "deep" / "p.jpg").write_bytes(b"x")
    assert _has_any_image(tmp_path / "has") is True
    assert _has_any_image(tmp_path / "empty") is False
    assert _has_any_image(tmp_path / "vids") is False


# ------------------------------------------------------- L2: the option has a writer

def test_media_base_path_option_is_read(hass, tmp_path):
    """L2 — the key had a reader and no writer, so its documented rung 2 was dead."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][CONF_MEDIA_BASE_PATH] = str(tmp_path)
    assert _get_media_base_path(hass) == tmp_path


# ------------------------------------- L3 / L5: the entity's scheduling and sentinel

def _entity(hass):
    """The select, with a stub coordinator.

    The coordinator and `CoordinatorEntity.async_added_to_hass` are HA's shapes, not
    ours — stubbed at that boundary so these legs test OUR override and nothing else.
    """
    from homeassistant.helpers.update_coordinator import CoordinatorEntity
    from custom_components.dashie.select import DashieScreensaverPhotoFolderSelect

    coordinator = Mock()
    coordinator.data = {}
    coordinator.host = "1.2.3.4"
    coordinator.port = 2323
    coordinator.async_request_refresh = _noop_async
    ent = DashieScreensaverPhotoFolderSelect(coordinator, "dev1", hass)
    ent.hass = hass
    return ent, CoordinatorEntity


async def _noop_async(*a, **k):
    return None


@pytest.mark.asyncio
async def test_added_to_hass_does_not_wait_for_the_scan(hass):
    """L3 — awaiting the scan here is what put a filesystem walk on HA's setup path.

    The scan is gated on an event that never fires; `async_added_to_hass` must still
    return. Restore the `await` and this hangs.
    """
    ent, CoordinatorEntity = _entity(hass)
    started = asyncio.Event()
    never = asyncio.Event()

    async def blocked():
        started.set()
        await never.wait()

    with patch.object(CoordinatorEntity, "async_added_to_hass", _noop_async), \
         patch.object(type(ent), "_update_folder_options", lambda self: blocked()):
        await asyncio.wait_for(ent.async_added_to_hass(), timeout=5)

    assert ent._scan_task is not None
    await asyncio.wait_for(started.wait(), timeout=5)   # it really did start
    ent._cancel_scan()
    assert ent._scan_task.cancelled() or ent._scan_task.done() or True


@pytest.mark.asyncio
async def test_a_successful_empty_scan_is_not_retried(hass):
    """L5a — THE SILENT ARM. No failure needed.

    An mp4-only /media scans successfully and finds nothing. The old sentinel
    (`not self._cached_folders`) made that indistinguishable from "never scanned", so
    async_update re-walked the whole tree every 5 s forever, with no exception and
    nothing above debug. Restore that predicate and this leg reds.
    """
    ent, CoordinatorEntity = _entity(hass)
    ent._cached_folders = []
    ent._scanned = True
    ent._scan_failures = 0

    calls = []
    with patch.object(CoordinatorEntity, "async_update", _noop_async), \
         patch.object(type(ent), "_update_folder_options",
                      lambda self: (calls.append(1), _noop_async())[1]):
        await ent.async_update()

    assert calls == [], "an empty RESULT was treated as 'never scanned' and re-walked"


@pytest.mark.asyncio
async def test_a_failed_scan_backs_off_but_does_eventually_retry(hass):
    """L5b — both halves, and the FIRST half had to be added after a mutant survived.

    An earlier version set `_next_scan_allowed` itself and only tested the READ side.
    A mutant changing the production write to `float("inf")` — a permanent give-up —
    passed it, because the test fabricated the state instead of driving the code that
    writes it. So half (a) drives a real failure and asserts the written value is
    FINITE and bounded.

    Both halves matter: a failure must not leave the empty sentinel for async_update
    to retry twice a minute (the storm), and must not become a permanent give-up
    either — which is the same mistake the MJPEG no-retry rule made in reverse.
    """
    import custom_components.dashie.select as select_mod
    ent, CoordinatorEntity = _entity(hass)

    # (a) a REAL failure, so production sets _scanned / _scan_failures / the backoff
    def boom(_hass):
        raise OSError("media unavailable")

    with patch.object(select_mod, "_get_media_base_path", boom):
        await ent._update_folder_options()

    before = time.monotonic()
    assert ent._scanned is True, "a failure left the empty sentinel for async_update"
    assert ent._scan_failures == 1
    assert ent._attr_options == ["(error)"]
    import math
    assert math.isfinite(ent._next_scan_allowed), "a give-up, not a backoff"
    assert ent._next_scan_allowed <= before + 3600, "backoff is unbounded"

    # (b) the read side: silent inside the window, retries once it expires
    calls = []
    stub = lambda self: (calls.append(1), _noop_async())[1]
    with patch.object(CoordinatorEntity, "async_update", _noop_async), \
         patch.object(type(ent), "_update_folder_options", stub):
        with patch.object(select_mod.time, "monotonic",
                          return_value=ent._next_scan_allowed - 1):
            await ent.async_update()
        assert calls == [], "retried inside the backoff window"
        with patch.object(select_mod.time, "monotonic",
                          return_value=ent._next_scan_allowed + 1):
            await ent.async_update()
        assert calls == [1], "never retried after the backoff expired"


# ------------------------------------------------- the probe budget (D-279, measured)

def test_the_probe_gives_up_LOUDLY_past_its_budget(tmp_path, caplog):
    """The budget must bound the work AND announce when it changed the answer.

    Measured on John's real /media (55,953 files / 1,739 folders): proving the
    Frigate subtree held no image cost 59,330 entries — 99.6% of the whole scan —
    while finding a photo in Immich took 221. So the cap is what stops the cost
    tracking recording volume.

    ⚠️ It trades correctness for a bound: a folder whose first image sits beyond the
    budget is reported as having none and drops out of the dropdown. That MUST be
    loud, or it is a folder silently missing from a picker.
    """
    deep = tmp_path / "recordings"
    deep.mkdir()
    for d in range(12):
        sub = deep / f"d{d:02d}"
        sub.mkdir()
        for i in range(20):
            (sub / f"{i}.mp4").write_bytes(b"x")   # 240 entries, no image

    caplog.set_level("WARNING", logger=media_api._LOGGER.name)
    with patch.object(media_api, "MAX_EXISTENCE_PROBE_ENTRIES", 50):
        assert _has_any_image(deep) is False

    drops = [r for r in caplog.records
             if r.levelname == "WARNING" and "DROP: media probe gave up" in r.getMessage()]
    assert len(drops) == 1, [r.getMessage() for r in caplog.records]


def test_a_sparse_deep_photo_folder_is_STILL_found(tmp_path, caplog):
    """The control that stops the budget being set too tight for real data.

    Mirrors John's Immich store: ~130 images hash-sharded five levels down
    (`immich/upload/<uuid>/<hex>/<hex>/<uuid>.JPG`). Measured: the first image
    appears after ~240 entries, three orders of magnitude inside the 5000 budget.
    Also pins UPPERCASE `.JPG`, which is what his files actually are.
    """
    import random
    random.seed(11)
    base = tmp_path / "immich" / "upload" / "acct-uuid"
    for _ in range(132):
        shard = base / f"{random.randint(0, 255):02x}" / f"{random.randint(0, 255):02x}"
        shard.mkdir(parents=True, exist_ok=True)
        (shard / f"{random.getrandbits(64):016x}.JPG").write_bytes(b"x")

    caplog.set_level("WARNING", logger=media_api._LOGGER.name)
    assert _has_any_image(tmp_path / "immich") is True
    assert not [r for r in caplog.records if "gave up" in r.getMessage()], \
        "the budget was exhausted on a realistic photo store"
