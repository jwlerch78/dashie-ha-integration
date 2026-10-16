"""The bridge-secret globs must match the slug forms HA actually creates.

Regression test for a defect that made bridge auth unenforceable: the glob was
`addon_configs/dashie*`, a PREFIX match, but HA names that directory after the
add-on's FULL slug — and a repo install prefixes it with a repo hash
(`62f754e2_dashie_ha`), a local install with `local_`, an official one with
`core_`. So the only thing it matched was a bare `dashie` directory, which HA
essentially never creates.

This is the same defect `_is_dashie_addon` was fixed for on 2026-07-30 ("match on
suffix, never equality alone"); the slug matcher was corrected and this glob, four
lines away, was not.
"""
from __future__ import annotations

import glob
from pathlib import Path

import pytest

from custom_components.dashie.addon_bridge import _BRIDGE_SECRET_GLOBS

# How HA names /config/addon_configs/<dir>, by install kind.
REAL_SLUG_DIRS = [
    pytest.param("62f754e2_dashie_ha", id="repo-install-prod"),
    pytest.param("62f754e2_dashie_ha_dev", id="repo-install-dev"),
    pytest.param("a1b2c3d4_dashie", id="repo-install-family-console"),
    pytest.param("local_dashie", id="local-install"),
    pytest.param("core_dashie", id="official-addon"),
    pytest.param("dashie", id="bare-slug"),
]


def _find(config_dir: Path) -> list[str]:
    """Resolve the globs the way _bridge_headers does, rooted at a fake config dir."""
    hits: list[str] = []
    for pattern in _BRIDGE_SECRET_GLOBS:
        hits.extend(sorted(glob.glob(str(config_dir / pattern))))
    return hits


@pytest.mark.parametrize("slug_dir", REAL_SLUG_DIRS)
def test_the_secret_is_found_for_every_real_install_kind(
    tmp_path: Path, slug_dir: str
) -> None:
    d = tmp_path / "addon_configs" / slug_dir
    d.mkdir(parents=True)
    (d / "bridge_secret").write_text("s3cret")
    assert _find(tmp_path), f"bridge secret unreachable for a {slug_dir} install"


def test_the_ha_config_dir_channel_still_works(tmp_path: Path) -> None:
    """The HA edition's second channel, which is why the bug was survivable."""
    d = tmp_path / ".dashie_voice"
    d.mkdir(parents=True)
    (d / "bridge_secret").write_text("s3cret")
    assert _find(tmp_path)


def test_a_foreign_addon_is_not_matched(tmp_path: Path) -> None:
    """`*dashie*` must not widen into other add-ons' private config dirs.

    Each add-on only sees its OWN addon_config mount, so reading someone else's
    would not work anyway — but a glob that tried would be a bug worth catching.
    """
    for other in ("core_mosquitto", "a1b2c3d4_frigate", "local_nodered"):
        d = tmp_path / "addon_configs" / other
        d.mkdir(parents=True)
        (d / "bridge_secret").write_text("not ours")
    assert _find(tmp_path) == []


def test_no_secret_anywhere_returns_nothing(tmp_path: Path) -> None:
    (tmp_path / "addon_configs").mkdir()
    assert _find(tmp_path) == []
