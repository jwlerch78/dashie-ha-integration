"""Every HTTP view this integration registers must require authentication.

An unauthenticated `HomeAssistantView` is reachable by anything that can open a
socket to the Home Assistant port. Two of them shipped that way from v1.3.0 until
1.5.1 — `/api/dashie/stream/mjpeg/{entity_id}` and `/api/dashie/stream/snapshot/{entity_id}`
served any camera in the house without a credential, behind a
`# TODO: restore to True after browser testing` that nobody came back to for six months.
It was closed once the Android client was shown to have sent `Authorization: Bearer`
since before either endpoint existed (`VideoFeedCard.kt:2156-2158`,
`VideoFeedThumbnailCache.kt:126-128`).

So the flag is not the thing worth protecting — the *TODO* is. A comment cannot fail a
test run; this can. Anything new that opens a view fails here until it is either fixed
or listed below with a reason and the work that closes it.

The scan is parsed, not grepped, so a renamed class, a moved file or a subclass declared
in a new module cannot slip past it.
"""
import ast
from pathlib import Path

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "dashie"

# (file, class) -> why it is still open, and what closes it.
ALLOWED_OPEN = {
    ("sensor_push.py", "DashieSensorPushView"):
        "Devices POST motion/face state here with NO credential of any kind "
        "(HaSensorPublisher.kt attaches no header at all), so flipping this alone would "
        "break every motion push in the field. The device already holds an HA OAuth token "
        "at CoreComponentInit.kt:349-358. Closes when an APK that sends it is in the field "
        "— client first, then this flag, never the reverse.",
}


def _view_classes() -> dict[tuple[str, str], bool | None]:
    """(file, class) -> requires_auth, for every HomeAssistantView subclass."""
    found: dict[tuple[str, str], bool | None] = {}
    for path in sorted(INTEGRATION.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=path.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(
                (isinstance(b, ast.Name) and b.id == "HomeAssistantView")
                or (isinstance(b, ast.Attribute) and b.attr == "HomeAssistantView")
                for b in node.bases
            ):
                continue
            requires_auth = None
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "requires_auth" for t in stmt.targets
                ):
                    try:
                        requires_auth = ast.literal_eval(stmt.value)
                    except ValueError:
                        requires_auth = "<not a literal>"
            found[(path.name, node.name)] = requires_auth
    return found


def test_no_view_is_unauthenticated_unless_listed():
    open_views = {k for k, v in _view_classes().items() if v is not True}
    unexpected = open_views - set(ALLOWED_OPEN)
    assert not unexpected, (
        "these views do not require authentication, so anything that can reach the Home "
        "Assistant port can call them; set requires_auth = True, or add an entry to "
        f"ALLOWED_OPEN saying why and what closes it: {sorted(unexpected)}"
    )


def test_the_allow_list_has_no_stale_entries():
    views = _view_classes()
    for key in ALLOWED_OPEN:
        assert key in views, f"allow-listed but no longer present: {key}"
        assert views[key] is not True, (
            f"{key} now requires auth — delete its ALLOWED_OPEN entry so the next open "
            "view cannot hide behind a stale exemption"
        )


def test_requires_auth_is_always_stated_explicitly():
    """An unset flag inherits a default; say it at the class, where a reader will look."""
    unset = [k for k, v in _view_classes().items() if v is None]
    assert not unset, f"these views never state requires_auth: {sorted(unset)}"


def test_the_stream_views_specifically_require_auth():
    """Named, because these two are the ones that shipped open for six months."""
    views = _view_classes()
    for name in ("DashieMjpegStreamView", "DashieSnapshotView"):
        assert views[("stream_proxy.py", name)] is True, f"{name} must require auth"


def test_the_scan_sees_an_open_view_added_to_a_real_file():
    """Self-test: a guard that cannot fail is not a guard (traps 4 and 16)."""
    source = (INTEGRATION / "stream_proxy.py").read_text()
    source += (
        "\n\nclass InjectedOpenView(HomeAssistantView):\n"
        '    url = "/api/dashie/injected"\n'
        '    name = "api:dashie:injected"\n'
        "    requires_auth = False\n"
    )
    tree = ast.parse(source)
    injected = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "InjectedOpenView"
    ]
    assert injected, "the injected class was not parsed at all"
    flag = [
        ast.literal_eval(s.value)
        for s in injected[0].body
        if isinstance(s, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "requires_auth" for t in s.targets)
    ]
    assert flag == [False], "the scan would not have seen an open view added here"
