"""Nothing new may call the boolean `send_command`.

`send_command` reports failure only as False, which nobody sees: that is how every
refused command came to look like it worked. Entity actions and services must use
`async_command` / `commands.async_send_to_all` (which raise) or, for background
pushes, `commands.async_push_to_all` (which logs). The few callers that are truly
fire-and-forget are listed below with the reason, and any other call fails this test.

The scan is by file and enclosing function (parsed, not grepped), so a moved call or a
new platform file cannot slip past it.
"""
import ast
from pathlib import Path

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "dashie"

ALLOWED = {
    ("coordinator.py", "_push_feed_trigger"):
        "video feed trigger pushed from a state-change listener; no user is waiting on it",
    ("coordinator.py", "_handle_legacy_trigger"):
        "legacy feed trigger scheduled with async_create_task; a raise would go unhandled",
}


def find_send_command_calls(sources: dict[str, str]) -> set[tuple[str, str]]:
    """(file, enclosing function) for every `<x>.send_command(...)` call."""
    found = set()
    for name, source in sources.items():
        tree = ast.parse(source, filename=name)

        def visit(node, function="<module>"):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = node.name
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "send_command"
            ):
                found.add((name, function))
            for child in ast.iter_child_nodes(node):
                visit(child, function)

        visit(tree)
    return found


def _integration_sources() -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(INTEGRATION.glob("*.py"))}


def test_only_allow_listed_callers_use_the_boolean_send_command():
    calls = find_send_command_calls(_integration_sources())
    unexpected = calls - set(ALLOWED)
    assert not unexpected, (
        "these call the boolean send_command, so a refused command would look like it "
        f"worked; use async_command / async_send_to_all / async_push_to_all: {sorted(unexpected)}"
    )


def test_the_allow_list_has_no_stale_entries():
    calls = find_send_command_calls(_integration_sources())
    assert set(ALLOWED) <= calls, f"allow-listed but no longer present: {sorted(set(ALLOWED) - calls)}"


def test_the_guard_catches_a_new_call_in_a_platform():
    """Self-test: the scan must see a call added to a real platform file."""
    sources = _integration_sources()
    assert find_send_command_calls({"button.py": sources["button.py"]}) == set()
    sources["button.py"] += (
        "\n\nclass Injected:\n"
        "    async def async_press(self):\n"
        "        await self.coordinator.send_command('rebootDevice')\n"
    )
    assert ("button.py", "async_press") in find_send_command_calls(sources) - set(ALLOWED)
