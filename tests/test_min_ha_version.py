"""The declared minimum Home Assistant version must cover the newest API we use.

`hacs.json`'s `homeassistant` key is what HACS uses to decide whether to OFFER an
update. It said 2024.1.0 while the code used `Platform.CONVERSATION`, which does not
exist before 2024.5.0 — and because that name is read at import time in `__init__.py`,
an older install does not lose one platform, it loses the whole integration: every
Dashie entity goes unavailable after an update HACS itself offered.

Two different numbers live here, and confusing them is the mistake this file exists to
prevent:

* the CODE floor — the oldest Home Assistant the code can actually load on. Derived, not
  chosen: FLOOR_EVIDENCE records, per API, the first version that has it, each verified by
  installing that version and importing every module. Today that is 2024.5.0.
* the DECLARED floor in hacs.json — a POLICY, deliberately set ABOVE the code floor to the
  version this suite is actually run against. John chose 2025.1.0 on 2026-09-17.

The declared floor being higher is therefore CORRECT and must not be "fixed" down to the
code floor: that would offer the update to installs nobody has ever tested it on. It may
only be lowered by deciding to support those versions and testing there.

The last test is the one that stops this recurring: adding a platform to PLATFORMS
fails until someone records which Home Assistant version introduced it.
"""
import ast
import json
import pathlib

INTEGRATION = pathlib.Path(__file__).parent.parent / "custom_components" / "dashie"
HACS_JSON = pathlib.Path(__file__).parent.parent / "hacs.json"

# The DECLARED floor: policy, not derivation. Raise it freely; lowering it means
# promising support for versions the suite is not run against.
DECLARED_FLOOR = "2025.1.0"  # John, 2026-09-17: "2025.1" — the version the suite runs on.

# The CODE floor: API the integration names → first Home Assistant version that has it.
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


def test_hacs_declares_the_agreed_floor():
    assert _declared_floor() == DECLARED_FLOOR, (
        f"hacs.json declares {_declared_floor()}, the agreed floor is {DECLARED_FLOOR}. "
        "This number is a decision (see DECLARED_FLOOR); change both together."
    )


def test_the_declared_floor_is_not_below_what_the_code_needs():
    """The policy floor may sit above the code floor. It may never sit below it."""
    highest = max(FLOOR_EVIDENCE.values(), key=_version_tuple)
    assert _version_tuple(DECLARED_FLOOR) >= _version_tuple(highest), (
        f"the declared floor {DECLARED_FLOOR} is below {highest}, which the code needs; "
        "HACS would offer the update to installs it breaks"
    )


def test_the_declared_floor_is_deliberately_above_the_code_floor():
    """Guard against a future reader 'fixing' the declared floor down to the code floor.

    2025.1.0 is not a mistake for 2024.5.0. The code floor is what the code can load on;
    the declared floor is what we are willing to support, and it is the only version the
    suite runs against. Lowering it to the code floor would ship to untested installs.
    """
    highest = max(FLOOR_EVIDENCE.values(), key=_version_tuple)
    assert _version_tuple(DECLARED_FLOOR) > _version_tuple(highest), (
        f"the declared floor now equals the code floor ({highest}). If that is deliberate — "
        "i.e. we are choosing to support and test that version — update DECLARED_FLOOR's "
        "comment and delete this test. Do not lower the floor just to make the numbers match."
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
