"""Registry access goes through `registry_compat`, and nothing else.

HA 2026.9 deprecated two things this integration used: `device_registry.devices` as a
mapping (`.values()`, `[id]`), and `async_get_device(identifiers=...)`. Both log for
custom integrations now and break in 2027.9. `registry_compat` has the versions that
work on 2025.1 and 2026.9 alike, and every caller is supposed to use it.

🔴 **WHY THIS IS A PARSED GUARD AND NOT A RUNTIME TEST.** The deprecation only *fires*
on HA 2026.9+. The venv this repo tests against by default is 2025.1.4, where a
deprecated call is simply a working call — so a runtime suite there is structurally
incapable of catching a new one, and would stay green while the breakage shipped. A
test that cannot run on the environment you have is not coverage. This one runs
anywhere, reads the source rather than executing it, and fails by construction.

The scan is by file and enclosing function (parsed, not grepped), so a moved call or a
new module cannot slip past it.
"""
import ast
from pathlib import Path

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "dashie"

# Attribute names that are the deprecated surface. `async_get_device_by_identifier` is the
# REPLACEMENT and is deliberately not here; the match is exact, so it is not caught.
DEPRECATED = {"async_get_device", "devices"}

ALLOWED = {
    ("registry_compat.py", "device_by_identifier"):
        "the old-HA fallback itself — this is the module the rest of the guard points at",
    ("registry_compat.py", "all_devices"):
        "the mapping-vs-iteration shim itself; the one place `.devices` is read on purpose",
    ("conversation.py", "_device_area_name"):
        "PRE-EXISTING AND SUSPECT, exempted to keep it VISIBLE rather than to bless it: it "
        "passes a device_id STRING where HA's signature takes `identifiers: set[tuple[str, str]]`, "
        "so the area lookup likely returns None silently and room awareness never resolves. "
        "Reported separately; not changed here because it is outside this unit and untested.",
}


def find_registry_access(sources: dict[str, str]) -> set[tuple[str, str]]:
    """(file, enclosing function) for every deprecated registry access."""
    found = set()
    for name, source in sources.items():
        tree = ast.parse(source, filename=name)

        def visit(node, function="<module>"):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = node.name
            if isinstance(node, ast.Attribute) and node.attr in DEPRECATED:
                found.add((name, function))
            for child in ast.iter_child_nodes(node):
                visit(child, function)

        visit(tree)
    return found


def _integration_sources() -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(INTEGRATION.glob("*.py"))}


def test_only_registry_compat_touches_the_deprecated_registry_surface():
    unexpected = find_registry_access(_integration_sources()) - set(ALLOWED)
    assert not unexpected, (
        "these reach the device registry directly instead of through registry_compat, so "
        "they log a deprecation on HA 2026.9 and break in 2027.9 — and the 2025.1 test venv "
        f"cannot see it: {sorted(unexpected)}"
    )


def test_the_guard_can_actually_fail():
    """Positive control: the matcher fires on the shape it is supposed to catch.

    Without this, a matcher that silently matched nothing would pass the test above
    forever and read as proof that nothing touches the registry.
    """
    caught = find_registry_access(
        {"fake.py": "def f(reg):\n    return reg.async_get_device(identifiers={('a', 'b')})\n"}
    )
    assert caught == {("fake.py", "f")}
    assert find_registry_access({"fake.py": "def f(reg):\n    return reg.async_get_device_by_identifier(x, y)\n"}) == set()
