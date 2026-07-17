"""View-level guard: what the gateway ACTUALLY PUTS ON THE WIRE to the brain.

`test_voice_payload.py` tests `build_brain_payload()` in isolation. That is necessary but not
sufficient: the original bug did not live in a pure function — it lived in the VIEW, in the
gap between what a caller sent and what got POSTed. A correct builder wired into the handler
wrongly (or bypassed) drops fields exactly as silently as the old allowlist did.

So these tests drive `DashieVoiceConverseView.post()` end-to-end with a mocked brain and assert
on the intercepted request body: the tablet's 10 fields go in, and the brain must receive them.
Both routes are covered — the cloud edge fn AND the on-prem add-on — because "My Local LLM"
routing every endpoint through this gateway is what widened the blast radius in the first place.

Background: `.reference/build-plans/20260716_HA_GATEWAY_PAYLOAD_ALLOWLIST_ROT.md` (dashieapp_staging).
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant

from custom_components.dashie.voice_view import BRAIN_URL, DashieVoiceConverseView

# The body the Dashie tablet actually sends (BrainConverseClient.kt ~155-190), minus `stream`
# (the streaming path is a separate transport — asserted in its own test below).
TABLET_BODY = {
    "text": "when do spain play argentina",
    "endpoint_id": "dashboard",
    "timezone": "America/New_York",
    "conversation_id": "conv-123",
    "history": [{"role": "user", "text": "hi"}],
    "provided_context": {"calendar": {"time_range": "today", "events": []}},
    "announcement": True,
    "client_fulfilled_tools": ["calendar", "weather", "calendar_write"],
    "language": "en",
    "retrieve_pictures": True,
    "options": {"model": "gemini-2.5-flash"},
}

TURN = {"ok": True, "type": "response", "voice": "Sunday at 3pm.", "text": None}


def _request(hass: HomeAssistant, body: dict) -> MagicMock:
    req = MagicMock()
    req.app = {"hass": hass}
    req.json = AsyncMock(return_value=body)
    return req


def _sent_to_brain(mock: aioresponses) -> dict:
    """The JSON body of the single POST the gateway made to the brain."""
    calls = [c for key, c in mock.requests.items() if str(key[1]).startswith(BRAIN_URL)]
    assert calls and calls[0], f"gateway never POSTed to the brain: {list(mock.requests)}"
    return calls[0][0].kwargs["json"]


async def test_cloud_route_forwards_every_field_the_tablet_sent(hass: HomeAssistant) -> None:
    """THE end-to-end guard. Measured 2026-07-16: tablet sent 11, brain got 7 — silently."""
    with aioresponses() as mock, patch(
        "custom_components.dashie.voice_view.get_account_credential",
        AsyncMock(return_value="jwt"),
    ), patch(
        "custom_components.dashie.voice_view.get_voice_config",
        AsyncMock(return_value={"route": "cloud"}),
    ):
        mock.post(BRAIN_URL, status=200, payload=TURN)
        resp = await DashieVoiceConverseView().post(_request(hass, dict(TABLET_BODY)))
        sent = _sent_to_brain(mock)

    assert resp.status == 200
    assert json.loads(resp.body) == TURN

    dropped = [k for k in TABLET_BODY if k not in sent]
    assert not dropped, f"gateway dropped field(s) en route to the brain: {dropped}"

    # The three that were dead in the field — asserted by VALUE, not just presence.
    assert sent["timezone"] == "America/New_York"          # else UTC "today", timeless sports
    assert sent["announcement"] is True                    # else a fired action re-schedules itself
    assert sent["client_fulfilled_tools"] == ["calendar", "weather", "calendar_write"]
    # …and the rest of the caller's payload, unmutated.
    assert sent["history"] == TABLET_BODY["history"]
    assert sent["provided_context"] == TABLET_BODY["provided_context"]
    assert sent["conversation_id"] == "conv-123"
    assert sent["language"] == "en"
    assert sent["retrieve_pictures"] is True
    assert sent["endpoint_id"] == "dashboard"


async def test_cloud_route_pins_retain_mode_and_strips_the_internal_route(
    hass: HomeAssistant,
) -> None:
    """§17 on the wire: the brain must be told 'caller' no matter what the caller asked for,
    and `options.route` is ours — it must not reach the brain."""
    body = {**TABLET_BODY, "options": {"model": "m", "retain_mode": "server", "route": "cloud"}}
    with aioresponses() as mock, patch(
        "custom_components.dashie.voice_view.get_account_credential",
        AsyncMock(return_value="jwt"),
    ):
        mock.post(BRAIN_URL, status=200, payload=TURN)
        await DashieVoiceConverseView().post(_request(hass, body))
        sent = _sent_to_brain(mock)

    assert sent["options"] == {"model": "m", "retain_mode": "caller"}


async def test_an_unknown_future_field_reaches_the_brain(hass: HomeAssistant) -> None:
    """Pass-through must hold through the VIEW, not just the builder — every field that was
    ever silently dropped began as one this gateway had never heard of."""
    body = {"text": "hi", "some_field_added_to_voicerequest_next_year": {"nested": [1, 2]}}
    with aioresponses() as mock, patch(
        "custom_components.dashie.voice_view.get_account_credential",
        AsyncMock(return_value="jwt"),
    ), patch(
        "custom_components.dashie.voice_view.get_voice_config",
        AsyncMock(return_value={"route": "cloud"}),
    ):
        mock.post(BRAIN_URL, status=200, payload=TURN)
        await DashieVoiceConverseView().post(_request(hass, body))
        sent = _sent_to_brain(mock)

    assert sent["some_field_added_to_voicerequest_next_year"] == {"nested": [1, 2]}


async def test_local_route_gets_the_same_full_payload(hass: HomeAssistant) -> None:
    """The on-prem brain is not a second-class caller: "My Local LLM" routes EVERY endpoint
    through here (that widening is what exposed the rot), so it gets the same fields."""
    body = {**TABLET_BODY, "options": {"model": "m", "route": "local"}}
    with patch(
        "custom_components.dashie.voice_view.converse_local",
        AsyncMock(return_value=(TURN, 200)),
    ) as converse_local:
        resp = await DashieVoiceConverseView().post(_request(hass, body))

    assert resp.status == 200
    sent = converse_local.await_args.args[1]
    dropped = [k for k in TABLET_BODY if k not in sent]
    assert not dropped, f"on-prem brain never received: {dropped}"
    assert sent["timezone"] == "America/New_York"
    assert sent["client_fulfilled_tools"] == ["calendar", "weather", "calendar_write"]
    assert sent["options"] == {"model": "m", "retain_mode": "caller"}  # route consumed here


async def test_account_config_decides_the_route_when_the_caller_omits_it(
    hass: HomeAssistant,
) -> None:
    """No options.route → ask the add-on (the single reader of user_settings). ai.model='local'
    is exactly how a logged-in tablet ends up on this path with no per-device flag."""
    with patch(
        "custom_components.dashie.voice_view.get_voice_config",
        AsyncMock(return_value={"route": "local"}),
    ), patch(
        "custom_components.dashie.voice_view.converse_local",
        AsyncMock(return_value=(TURN, 200)),
    ) as converse_local:
        await DashieVoiceConverseView().post(_request(hass, dict(TABLET_BODY)))

    assert converse_local.await_args.args[1]["timezone"] == "America/New_York"


async def test_streaming_path_forwards_the_full_payload(hass: HomeAssistant) -> None:
    """The tablet sets stream:true, so the NDJSON path is the REAL production path for it —
    a drop here would be invisible to every non-streaming test."""
    body = {**TABLET_BODY, "stream": True}
    ndjson = b'{"kind":"stage","stage":"routed"}\n{"kind":"final","turn":{"ok":true}}\n'
    with aioresponses() as mock, patch(
        "custom_components.dashie.voice_view.get_account_credential",
        AsyncMock(return_value="jwt"),
    ), patch(
        "custom_components.dashie.voice_view.get_voice_config",
        AsyncMock(return_value={"route": "cloud"}),
    ):
        mock.post(BRAIN_URL, status=200, body=ndjson)
        req = _request(hass, body)
        req.transport = MagicMock()  # StreamResponse.prepare() needs a live transport
        with patch("aiohttp.web.StreamResponse.prepare", AsyncMock()), patch(
            "aiohttp.web.StreamResponse.write", AsyncMock()
        ), patch("aiohttp.web.StreamResponse.write_eof", AsyncMock()):
            await DashieVoiceConverseView().post(req)
        sent = _sent_to_brain(mock)

    dropped = [k for k in TABLET_BODY if k not in sent]
    assert not dropped, f"streaming path dropped: {dropped}"
    assert sent["stream"] is True
    assert sent["timezone"] == "America/New_York"
