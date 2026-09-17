"""The declared minimum Home Assistant version must cover the newest API we use.

`hacs.json`'s `homeassistant` key is what HACS uses to decide whether to OFFER an
update. It said 2024.1.0 while the code used `Platform.CONVERSATION`, which does not
exist before 2024.5.0 — and because that name is read at import time in `__init__.py`,
an older install does not lose one platform, it loses the whole integration: every
Dashie entity goes unavailable after an update HACS itself offered.

The declared floor is therefore evidence-backed here. FLOOR_EVIDENCE records, per API,
the first Home Assistant version that has it, each verified by installing that version
and importing the integration (see the thread notes for the probe). The tests keep the
three things in lockstep: the declared floor, the evidence, and the source.

The last test is the one that stops this recurring: adding a platform to PLATFORMS
fails until someone records which Home Assistant version introduced it.
"""
import ast
import json
import pathlib

INTEGRATION = pathlib.Path(__file__).parent.parent / "custom_components" / "dashie"
HACS_JSON = pathlib.Path(__file__).parent.parent / "hacs.json"

# API the integration names → first Home Assistant version that has it.
FLOOR_EVIDENCE = {
    # Import probe: 2024.4.4 raises AttributeError on Platform.CONVERSATION (so the
    # integration does not load at all); 2024.5.0 imports every module cleanly.
    "Platform.CONVERSATION": "2024.5.0",
    # Same probe: conversation.ConversationEntity is absent in 2024.4.4, present in 2024.5.0.
    "conversation.ConversationEntity": "2024.5.0",
}

# Platforms old enough to predate the floor above. A platform NOT listed here and not in
# FLOOR_EVIDENCE fails the last test on purpose.
PLATFORMS_PREDATING_FLOOR = {
    "BINARY_SENSOR", "BUTTON", "CAMERA", "IMAGE", "MEDIA_PLAYER", "NUMBER",
    "SELECT", "SENSOR", "SWITCH", "TEXT", "UPDATE",
}


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def _declared_floor() -> str:
    return json.loads(HACS_JSON.read_text())["homeassistant"]


def _platforms_used() -> set[str]:
    """The Platform.* members listed in PLATFORMS in __init__.py."""
    tree = ast.parse((INTEGRATION / "__init__.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "PLATFORMS":
            return {
                e.attr for e in node.value.elts
                if isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name)
                and e.value.id == "Platform"
            }
    raise AssertionError("PLATFORMS not found in __init__.py")


def test_declared_floor_covers_every_recorded_api():
    highest = max(FLOOR_EVIDENCE.values(), key=_version_tuple)
    assert _version_tuple(_declared_floor()) >= _version_tuple(highest), (
        f"hacs.json declares {_declared_floor()} but the code uses an API that needs "
        f"{highest}; HACS would offer this to installs it breaks"
    )


def test_the_floor_is_not_higher_than_the_evidence_supports():
    """A floor nobody derived turns away users for no reason."""
    highest = max(FLOOR_EVIDENCE.values(), key=_version_tuple)
    assert _version_tuple(_declared_floor()) == _version_tuple(highest), (
        f"hacs.json declares {_declared_floor()}, evidence supports {highest}. Raising the "
        "floor deliberately is fine — record why in FLOOR_EVIDENCE so it stays derived."
    )


def test_every_recorded_api_is_still_used():
    """Prune the evidence when an API goes away, so the floor can come back down."""
    source = "\n".join(p.read_text() for p in INTEGRATION.glob("*.py"))
    for api in FLOOR_EVIDENCE:
        assert api in source, f"{api} is recorded as setting the floor but is no longer used"


def test_a_new_platform_must_record_the_version_it_needs():
    unaccounted = {
        p for p in _platforms_used()
        if p not in PLATFORMS_PREDATING_FLOOR
        and f"Platform.{p}" not in FLOOR_EVIDENCE
    }
    assert not unaccounted, (
        f"these platforms have no recorded Home Assistant floor: {sorted(unaccounted)}. "
        "Find the first version that defines each (install it and import the integration), "
        "add it to FLOOR_EVIDENCE, and raise hacs.json to match."
    )
