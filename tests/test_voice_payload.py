"""Guards the voice gateway's brain payload against ALLOWLIST ROT.

THE BUG CLASS THIS EXISTS FOR
-----------------------------
`voice_view.py` builds the `POST /api/dashie/voice/converse` payload for the brain
(`voice-conversation/types.ts VoiceRequest`). It used to name each field it forwarded:

    for key in ("history", "provided_context", "conversation_id"):
        if body.get(key) is not None:
            payload[key] = body[key]

Anything the caller sent that wasn't on that list was DISCARDED SILENTLY — no error, no
log. The brain simply received less and behaved differently, which reads as a model quirk,
not a bug. The gateway was written for one HEADLESS HA voice satellite; later, selecting
"My Local LLM" routed EVERY endpoint through it (including the logged-in tablet, which
sends 11 fields — only 7 survived) and nobody re-read the payload builder. Three dead
fields were found by inspection: `client_fulfilled_tools` (erased to []), `timezone` (→ a
UTC "today" and sports answers with no kickoff time), `announcement` (→ a fired scheduled
action could re-schedule itself and compound on every fire).

Finding them by reading is not a strategy. So the default is now INVERTED: the body is
forwarded wholesale and the gateway overrides only what it owns.

WHAT THESE TESTS ARE FOR
------------------------
`test_forwards_an_unknown_future_field` is the load-bearing one, and it deliberately does
NOT know the VoiceRequest contract — it asserts the pass-through PROPERTY with a field name
that exists nowhere. It cannot rot as the contract grows, and it fails the moment anyone
reintroduces an allowlist. The rest pin the gateway-owned overrides that pass-through must
not be allowed to defeat (chiefly §17 retain_mode).

The complementary half is `npm run lint:gateway-payload` in dashieapp_staging, which
cross-checks GATEWAY_OWNED_KEYS against types.ts (this repo can't see it). Registered as
contract #30 in `.reference/JS_KOTLIN_CONTRACTS.md`.
"""
from custom_components.dashie.voice_view import (
    GATEWAY_OWNED_KEYS,
    GATEWAY_RETAIN_MODE,
    build_brain_payload,
)

# What the Dashie tablet actually sends (BrainConverseClient.kt ~155-190). Not a contract
# mirror — a realistic body, so a drop shows up as a failure naming the real field.
TABLET_BODY = {
    "text": "when do spain play argentina",
    "endpoint_id": "dashboard",
    "stream": True,
    "timezone": "America/New_York",
    "conversation_id": "conv-123",
    "history": [{"role": "user", "text": "hi"}],
    "provided_context": {"calendar": {"time_range": "today", "events": []}},
    "announcement": True,
    "client_fulfilled_tools": ["calendar", "weather", "calendar_write"],
    "language": "en",
    "retrieve_pictures": True,
    "options": {"model": "gemini-2.5-flash", "personality_id": "p1"},
}


def test_forwards_an_unknown_future_field() -> None:
    """THE regression guard: a field the gateway has never heard of must reach the brain.

    Every silently-dropped field started life as a field this gateway had never heard of.
    An allowlist — of any shape, however well-intentioned — fails this test.
    """
    payload, _endpoint_id, _route = build_brain_payload(
        {"text": "hi", "some_field_added_to_voicerequest_next_year": {"nested": [1, 2]}}
    )
    assert payload["some_field_added_to_voicerequest_next_year"] == {"nested": [1, 2]}


def test_forwards_every_caller_owned_field_the_tablet_sends() -> None:
    """No caller-owned field may vanish. Names the casualties if one does."""
    payload, _endpoint_id, _route = build_brain_payload(TABLET_BODY)

    dropped = [
        k for k in TABLET_BODY
        if k not in GATEWAY_OWNED_KEYS and k not in payload
    ]
    assert not dropped, f"gateway dropped caller field(s): {dropped}"

    for key, value in TABLET_BODY.items():
        if key not in GATEWAY_OWNED_KEYS:
            assert payload[key] == value, f"gateway mutated caller field {key!r}"


def test_pins_retain_mode_even_when_the_caller_asks_for_server() -> None:
    """§17 privacy invariant: a caller must never be able to move family speech into
    Supabase. Pass-through copies `options` — retain_mode is pinned AFTER, so it wins."""
    payload, _endpoint_id, _route = build_brain_payload(
        {"text": "hi", "options": {"retain_mode": "server", "model": "m"}}
    )
    assert payload["options"]["retain_mode"] == GATEWAY_RETAIN_MODE == "caller"
    assert payload["options"]["model"] == "m"  # the caller's other options still ride along


def test_keeps_caller_options_and_adds_retain_mode() -> None:
    payload, _endpoint_id, _route = build_brain_payload(TABLET_BODY)
    assert payload["options"] == {
        "model": "gemini-2.5-flash",
        "personality_id": "p1",
        "retain_mode": "caller",
    }


def test_non_dict_options_is_treated_as_absent_not_a_crash() -> None:
    """A LAN caller can send anything; a junk `options` must not 500 the turn."""
    payload, _endpoint_id, _route = build_brain_payload({"text": "hi", "options": "nonsense"})
    assert payload["options"] == {"retain_mode": "caller"}


def test_route_is_returned_and_stripped_from_the_brain_payload() -> None:
    """`options.route` is gateway-internal (cloud vs on-prem, §13.17) — the brain has no
    such field, so it is consumed here and not forwarded."""
    payload, _endpoint_id, route = build_brain_payload(
        {"text": "hi", "options": {"route": "local", "model": "m"}}
    )
    assert route == "local"
    assert "route" not in payload["options"]
    assert "route" not in payload
    assert payload["options"] == {"model": "m", "retain_mode": "caller"}


def test_route_absent_reads_as_none_so_the_account_config_decides() -> None:
    _payload, _endpoint_id, route = build_brain_payload(TABLET_BODY)
    assert route is None


def test_declared_client_tools_win() -> None:
    """The tablet DOES declare capabilities; hard-coding [] told the brain it could
    fulfill nothing (device tools dropped from the prompt, calendar_write declined)."""
    payload, _endpoint_id, _route = build_brain_payload(TABLET_BODY)
    assert payload["client_fulfilled_tools"] == ["calendar", "weather", "calendar_write"]


def test_an_explicitly_empty_declaration_is_honoured() -> None:
    """`[]` is a real declaration (a headless satellite saying "I can run nothing"), not
    an absence — it must not be confused with the default."""
    payload, _endpoint_id, _route = build_brain_payload(
        {"text": "hi", "client_fulfilled_tools": []}
    )
    assert payload["client_fulfilled_tools"] == []


def test_a_non_declaring_caller_gets_the_headless_default() -> None:
    """The one field pass-through must NOT pass through: the brain reads absent as "this
    caller fulfills everything", right for a tablet, wrong for the HA satellite."""
    payload, _endpoint_id, _route = build_brain_payload({"text": "hi"})
    assert payload["client_fulfilled_tools"] == []


def test_endpoint_id_defaults_to_the_satellite_id() -> None:
    payload, endpoint_id, _route = build_brain_payload({"text": "hi"})
    assert endpoint_id == "ha-voice"
    assert payload["endpoint_id"] == "ha-voice"


def test_caller_endpoint_id_is_kept() -> None:
    payload, endpoint_id, _route = build_brain_payload(TABLET_BODY)
    assert endpoint_id == "dashboard"
    assert payload["endpoint_id"] == "dashboard"


def test_does_not_mutate_the_callers_body() -> None:
    """The view reads `body` after building the payload (e.g. `body.get("stream")`)."""
    body = {"text": "hi", "options": {"route": "local"}}
    build_brain_payload(body)
    assert body == {"text": "hi", "options": {"route": "local"}}
