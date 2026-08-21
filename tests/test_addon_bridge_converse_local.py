"""Guards the on-prem brain call against the two defects that made every local-route
turn come back EMPTY — and made the emptiness untraceable.

THE BUG CLASS THIS EXISTS FOR
-----------------------------
`converse_local` POSTs a transcript to the add-on's `/api/voice/converse-local`, which
answers with ONE buffered JSON turn. Its caller (`voice_view`'s local-route branch) hands
the result straight to `web.json_response` — there is no NDJSON path there, unlike the
cloud branch which deliberately splits on `body.stream`.

But the DEVICE sets `stream: true` on every turn (`BrainConverseClient.kt`), and the whole
payload was forwarded verbatim. Asked to stream, the add-on answered with an empty body.
aiohttp's `.json()` returns **None** for an empty body, and the code did:

    return (body or {}), status

so None became `{}` and went back as a perfectly ordinary **HTTP 200 with an empty turn**.
A/B'd to certainty by Thread T on one field: `{"text":…}` → a full valid turn;
`{"text":…,"stream":true}` → `{}`. Every local-route turn from a real device was empty.

The second half is why it cost a live debugging session rather than a log line: the `or {}`
captured nothing. The integration logged nothing, HA's error_log had no Dashie entry, and
the only evidence anywhere in the system was the device's own blank-turn DROP. A failure
that erases its own cause is worse than a loud crash.

WHAT THESE TESTS PIN
--------------------
`test_stream_is_not_forwarded` is the load-bearing one — it asserts the property (this
endpoint never receives a flag it cannot honour) rather than re-describing the payload
contract, so it cannot rot as fields are added.

The unparseable-body tests assert the RULE, not the symptom: a body we cannot use must
never be laundered into a success. If someone reintroduces `or {}` to "be tolerant", these
fail. The good-turn test is the positive control — without it, every assertion here is
satisfiable by a function that always errors.
"""
import json

import pytest

from custom_components.dashie import addon_bridge


class _FakeResp:
    """Faithful to aiohttp where it matters: `.json()` yields None for an empty body,
    which is exactly how an empty 200 became `{}`."""

    def __init__(self, status: int, text: str):
        self.status = status
        self._text = text

    async def text(self) -> str:
        return self._text

    async def json(self, content_type=None, **_kw):
        stripped = (self._text or "").strip()
        return json.loads(stripped) if stripped else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _FakeSession:
    def __init__(self, status: int, text: str):
        self._status = status
        self._text = text
        self.sent_payload = None
        self.sent_url = None

    def post(self, url, json=None, timeout=None):  # noqa: A002
        self.sent_url = url
        self.sent_payload = json
        return _FakeResp(self._status, self._text)


@pytest.fixture
def wire(monkeypatch):
    """Point converse_local at a fake add-on and a fixed base."""

    def _wire(status: int, text: str):
        session = _FakeSession(status, text)
        monkeypatch.setattr(addon_bridge, "async_get_clientsession", lambda _hass: session)

        async def _bases(_session):
            return ["http://addon.local:8099"]

        monkeypatch.setattr(addon_bridge, "_resolve_bases", _bases)
        return session

    return _wire


async def test_stream_is_not_forwarded(wire):
    """The device always sends stream:true; this endpoint cannot honour it."""
    session = wire(200, json.dumps({"type": "response", "text": "hello"}))
    payload = {"text": "what time is it", "stream": True, "options": {"x": 1}}

    await addon_bridge.converse_local(object(), payload)

    assert "stream" not in session.sent_payload, (
        "stream reached the add-on: it answers with an empty body, which the caller "
        "then reports as a successful empty turn"
    )
    # Everything else must still get through — the fix is a strip, not a filter.
    assert session.sent_payload["text"] == "what time is it"
    assert session.sent_payload["options"] == {"x": 1}
    # And the caller's own dict is not mutated out from under it.
    assert payload["stream"] is True


async def test_good_turn_is_returned_unchanged(wire):
    """POSITIVE CONTROL: without this, every other test here passes for a function
    that never succeeds at all."""
    wire(200, json.dumps({"type": "response", "text": "hello from the local model"}))

    turn, status = await addon_bridge.converse_local(object(), {"text": "hi"})

    assert status == 200
    assert turn["text"] == "hello from the local model"


async def test_empty_body_is_not_laundered_into_an_empty_200(wire):
    """The production symptom, exactly: HTTP 200 with `{}`."""
    wire(200, "")

    turn, status = await addon_bridge.converse_local(object(), {"text": "hi"})

    assert turn != {}, "an empty body came back as an empty successful turn again"
    assert status != 200, "a body we could not read must not be reported as success"
    assert turn["error"] == "converse_local_unparseable"


async def test_unparseable_body_says_what_came_back(wire):
    """A drop that names nothing is why this took a live session to find."""
    wire(200, "data: {\"kind\":\"stage\"}\n\ndata: {\"kind\":\"final\"}\n\n")

    turn, _status = await addon_bridge.converse_local(object(), {"text": "hi"})

    assert turn["error"] == "converse_local_unparseable"
    assert turn["status"] == 200
    assert "data:" in turn["body_preview"], "the preview must carry the actual body"


async def test_upstream_error_status_is_preserved(wire):
    """A real 500 keeps its own status rather than being renamed to 502."""
    wire(500, "upstream exploded")

    turn, status = await addon_bridge.converse_local(object(), {"text": "hi"})

    assert status == 500
    assert turn["error"] == "converse_local_unparseable"
