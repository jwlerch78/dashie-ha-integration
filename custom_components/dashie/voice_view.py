"""Dashie voice gateway — routes a transcript to the voice-conversation brain.

An anonymous kiosk tablet (and later generic HA voice satellites) can't call the
cloud brain directly — it has no account credential. This view, authed by the HA
token the device already holds, gets the account credential from the add-on and
calls the brain on the account's behalf, returning the turn. The device then
speaks the result and dispatches any HA action natively.

This is the runtime gateway for WS3 (build plan §3.2). Reachable on-LAN and via
remote HA URLs because it rides HA's own :8123 API surface.

HTTP endpoint:
  POST /api/dashie/voice/converse
    { text, endpoint_id?, conversation_id?, history?, provided_context?, options? }
  → the brain's turn { type, voice, text, action, usage, stages, ... }

Deferred (later slices): /voice/session token bundle, STT-token minting, and
TTS in the gateway (v1 returns text; the tablet speaks it natively).
"""
from __future__ import annotations

import json
import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .addon_bridge import (
    AddonUnavailable,
    SharingDisabled,
    authorize_device,
    converse_local,
    get_account_credential,
    get_sharing_status,
    get_voice_config,
)

_LOGGER = logging.getLogger(__name__)

# The voice-conversation brain edge function.
# TODO(config): derive per-environment (staging vs prod) instead of hardcoding.
BRAIN_URL = "https://cwglbtosingboqepsmjk.supabase.co/functions/v1/voice-conversation"

# Mints the short-lived, STT-scoped credential an anonymous tablet streams Deepgram with.
# TODO(config): derive per-environment alongside BRAIN_URL.
ISSUE_STT_TOKEN_URL = "https://cwglbtosingboqepsmjk.supabase.co/functions/v1/issue-stt-token"


class DashieVoiceConverseView(HomeAssistantView):
    """Authed by the HA token; calls the brain on the account's behalf."""

    url = "/api/dashie/voice/converse"
    name = "api:dashie:voice:converse"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

        text = (body or {}).get("text")
        if not text or not isinstance(text, str):
            return web.json_response({"ok": False, "error": "text_required"}, status=400)

        endpoint_id = body.get("endpoint_id") or "ha-voice"
        # Caller-mode retention (§17): the brain must NEVER persist transcript text
        # to Supabase for kiosk turns — it only signals metadata.retain_transcript,
        # and we keep the transcript HA-locally below.
        options = dict(body.get("options") or {})
        options["retain_mode"] = "caller"
        payload = {
            "text": text,
            "endpoint_id": endpoint_id,
            "options": options,
            # This gateway is HEADLESS — it has no device to run a client_tool (weather,
            # calendar, …). Declaring an empty capability list tells the brain to
            # self-fulfill server-side where it can (weather → edge Open-Meteo) instead of
            # handing back an unfulfillable client_tool, so "weather this weekend" answers
            # instead of dead-ending. A capable caller (the Dashie tablet) omits this field.
            "client_fulfilled_tools": [],
        }
        for key in ("history", "provided_context", "conversation_id"):
            if body.get(key) is not None:
                payload[key] = body[key]

        # ── Route selection: cloud brain (default) vs on-prem add-on brain ─────
        # Precedence (build plan §13.17):
        #   1. explicit options.route — a per-request override (the kiosk dev toggle / harness),
        #   2. else the ACCOUNT's selected model — "My Local LLM" (ai.model=='local') → local.
        # The add-on is the single reader of user_settings; we just ask it for the route. So
        # selecting "My Local LLM" in the Console routes every endpoint here with no per-device flag.
        route = options.get("route")
        if route is None:
            route = (await get_voice_config(hass)).get("route", "cloud")
        if route == "local":
            try:
                turn, status = await converse_local(hass, payload)
            except SharingDisabled:
                return web.json_response({"ok": False, "error": "sharing_disabled"}, status=403)
            except AddonUnavailable as err:
                return web.json_response({"ok": False, "error": f"addon_unavailable: {err}"}, status=503)
            await self._maybe_retain(hass, turn, text, endpoint_id, payload)
            return web.json_response(turn, status=(200 if status < 400 else status))

        # ── Cloud brain path ──────────────────────────────────────────────────
        # Gated on the add-on's household-sharing opt-in: get_account_credential
        # raises SharingDisabled (403) when the account holder hasn't enabled it.
        try:
            cred = await get_account_credential(hass)  # account JWT, from the add-on
        except SharingDisabled:
            return web.json_response({"ok": False, "error": "sharing_disabled"}, status=403)
        except AddonUnavailable as err:
            return web.json_response({"ok": False, "error": f"addon_unavailable: {err}"}, status=503)

        brain_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cred}",
            "apikey": cred,
        }
        session = async_get_clientsession(hass)

        # COMPAT: only proxy the NDJSON progress stream when the CLIENT asked for it
        # (`body.stream` — the streaming-aware tablet APK). An older APK does a plain-JSON
        # parse and would choke on NDJSON, so it keeps the buffered single-turn path.
        if not body.get("stream"):
            try:
                async with session.post(BRAIN_URL, json=payload, headers=brain_headers) as resp:
                    turn = await resp.json(content_type=None)
                    status = 200 if resp.status < 400 else resp.status
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("voice converse → brain failed: %s", err)
                return web.json_response({"ok": False, "error": f"brain_call_failed: {err}"}, status=502)
            await self._maybe_retain(hass, turn, text, endpoint_id, payload)
            return web.json_response(turn, status=status)

        # STREAM the brain's NDJSON straight through so the kiosk shows live progress
        # (thinking → "Searching the web…" → "Finalizing…") — one JSON object per line:
        # {kind:'stage',…} events, then {kind:'final',turn}. Forward each line as it arrives
        # and capture the final turn for caller-side transcript retention.
        payload["stream"] = True
        downstream = web.StreamResponse(
            headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-cache"}
        )
        prepared = False
        final_turn = None
        try:
            async with session.post(BRAIN_URL, json=payload, headers=brain_headers) as resp:
                if resp.status >= 400:
                    # Error before any stream — relay it as a plain JSON body, not a stream.
                    err_body = await resp.text()
                    return web.Response(status=resp.status, text=err_body, content_type="application/json")
                await downstream.prepare(request)
                prepared = True
                async for raw in resp.content:  # NDJSON is line-delimited
                    if not raw.strip():
                        continue
                    await downstream.write(raw if raw.endswith(b"\n") else raw + b"\n")
                    try:
                        obj = json.loads(raw)
                        if obj.get("kind") == "final":
                            final_turn = obj.get("turn")
                    except Exception:  # noqa: BLE001 — a partial/odd line just isn't the final turn
                        pass
            await downstream.write_eof()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("voice converse → brain stream failed: %s", err)
            if not prepared:
                return web.json_response({"ok": False, "error": f"brain_call_failed: {err}"}, status=502)
            try:
                await downstream.write_eof()
            except Exception:  # noqa: BLE001
                pass

        if final_turn is not None:
            await self._maybe_retain(hass, final_turn, text, endpoint_id, payload)
        return downstream

    @staticmethod
    async def _maybe_retain(hass, turn, text, endpoint_id, payload) -> None:
        """Store the transcript HA-locally only when the account opted in (the brain
        signals it via metadata.retain_transcript). Never blocks/breaks the turn.
        Shared by both the cloud and on-prem brain paths."""
        try:
            meta = turn.get("metadata") if isinstance(turn, dict) else None
            if meta and meta.get("retain_transcript"):
                store = hass.data.get("dashie", {}).get("transcript_store")
                if store is not None:
                    await store.async_append(
                        text=text,
                        voice=turn.get("voice") or "",
                        subtext=turn.get("text") or None,
                        endpoint_id=endpoint_id,
                        session_id=turn.get("conversation_id") or payload.get("conversation_id"),
                    )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("transcript retention skipped: %s", err)


class DashieVoiceStatusView(HomeAssistantView):
    """Capability probe — can this HA offer Dashie Cloud voice to anonymous endpoints?

    Authed by the HA token. Returns `{available, reason}` so an anonymous kiosk
    tablet can decide whether to surface "Dashie Cloud" (the right-hand side of
    the §16.5 OR). No credential is ever returned.
    """

    url = "/api/dashie/voice/status"
    name = "api:dashie:voice:status"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        status = await get_sharing_status(hass)
        # Household conversation agent mode (live|dialog|single) — the console's Voice & AI
        # page writes it to the account; the add-on reads it (voice-config); we forward it so
        # an anonymous kiosk behaves like the account chose (Live-on-kiosk, 2026-07-09).
        # Best-effort: '' when the add-on doesn't report one (older add-on / unreachable) —
        # the tablet then uses its kiosk default.
        agent_mode = ""
        retrieve_pictures = None
        brain_route = ""
        brain_route_reason = ""
        # WS-G account defaults + full voice pipeline mirror (kiosk Phase 1) — carried so a
        # share-account anon kiosk reflects the household's Voice & AI setup, not just agentMode.
        cfg: dict = {}
        try:
            cfg = await get_voice_config(hass)
            agent_mode = cfg.get("agent_mode", "") or ""
            # ai.retrievePicturesEnabled (relay image_search gate) — forwarded only when
            # the add-on reports a boolean; older add-ons omit it.
            if isinstance(cfg.get("retrieve_pictures"), bool):
                retrieve_pictures = cfg["retrieve_pictures"]
            # Where the account's BRAIN runs (Open Brain §5): 'local' when the add-on serves
            # it (own model / Hermes / BYO provider key on the box), 'cloud' for the metered
            # edge fn. A logged-in device reads this to send cascade turns through this
            # gateway instead of direct-to-cloud. '' when the add-on doesn't report one
            # (older add-on) — the device then keeps its default (direct cloud).
            if cfg.get("route") in ("local", "cloud"):
                brain_route = cfg["route"]
                brain_route_reason = cfg.get("route_reason", "") or ""
        except Exception:  # noqa: BLE001 — never fail the status probe on config
            agent_mode = ""
            cfg = {}
        body = {
            "available": bool(status.get("available")),
            "reason": status.get("reason", "unknown"),
            "agent_mode": agent_mode,
        }
        # Account identity for the anon-kiosk "Authorized by <email>" display —
        # household-sharing-driven. The add-on only vends account_email when sharing
        # is available, so forward it as-is when present (absent → kiosk shows nothing).
        account_email = status.get("account_email")
        if isinstance(account_email, str) and account_email:
            body["account_email"] = account_email
        if retrieve_pictures is not None:
            body["retrieve_pictures"] = retrieve_pictures
        if brain_route:
            body["brain_route"] = brain_route
            body["brain_route_reason"] = brain_route_reason
        # WS-G defaults (personality/voice/wake word) — forwarded as-is; the native
        # anon-kiosk resolver applies them. Only included when non-empty so an older
        # add-on that omits them leaves the kiosk's current value alone.
        for src_key, out_key in (
            ("default_personality_id", "default_personality_id"),
            ("default_voice_key", "default_voice_key"),
            ("default_wake_word", "default_wake_word"),
        ):
            val = cfg.get(src_key)
            if isinstance(val, str) and val:
                body[out_key] = val
        # Full voice pipeline block (STT/TTS providers + HA engine ids + voice + model +
        # search source + preset) — the account's Voice & AI setup mirrored to the kiosk.
        pipeline = cfg.get("pipeline")
        if isinstance(pipeline, dict) and pipeline:
            body["pipeline"] = pipeline
        # ai.model rides alongside the pipeline (the kiosk's brain model mirrors the account).
        model = cfg.get("model")
        if isinstance(model, str) and model:
            body["model"] = model
        return web.json_response(body)


class DashieVoiceSessionView(HomeAssistantView):
    """Vend a short-lived STT token bundle to an anonymous endpoint (build plan §3.2 WS2).

    Authed by the HA token the tablet already holds. Gets the account credential from the
    add-on (gated on household-sharing), mints an STT-scoped token via `issue-stt-token`,
    and returns `{stt_token, endpoint_id, expires_at}`. The tablet streams `deepgram-stt-stream`
    directly with `stt_token` (server VAD preserved, no per-utterance latency), and continues
    to send its transcript to the converse view (HA-token authed) — so no full account JWT ever
    reaches the device. Deepgram quality on an anonymous tablet, denial-of-wallet hole closed.
    """

    url = "/api/dashie/voice/session"
    name = "api:dashie:voice:session"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        endpoint_id = (body or {}).get("endpoint_id") or "ha-voice"

        # Account credential from the add-on — raises SharingDisabled (403) when the account
        # holder hasn't enabled household sharing, AddonUnavailable (503) when unreachable.
        try:
            cred = await get_account_credential(hass)
        except SharingDisabled:
            return web.json_response({"ok": False, "error": "sharing_disabled"}, status=403)
        except AddonUnavailable as err:
            return web.json_response({"ok": False, "error": f"addon_unavailable: {err}"}, status=503)

        session = async_get_clientsession(hass)
        mint_body = {"endpoint_id": endpoint_id}
        if body.get("ttl_seconds") is not None:
            mint_body["ttl_seconds"] = body["ttl_seconds"]
        try:
            async with session.post(
                ISSUE_STT_TOKEN_URL,
                json=mint_body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {cred}",
                    "apikey": cred,
                },
            ) as resp:
                bundle = await resp.json(content_type=None)
                status = resp.status
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("voice session → issue-stt-token failed: %s", err)
            return web.json_response({"ok": False, "error": f"mint_failed: {err}"}, status=502)

        if status >= 400:
            return web.json_response({"ok": False, "error": "mint_rejected", "detail": bundle}, status=status)
        return web.json_response(bundle, status=200)


class DashieAccountAuthorizeView(HomeAssistantView):
    """Authorize a LAN kiosk tablet into the household Dashie account (Kiosk Real Login, Phase 1).

    The tablet creates a device code with Dashie's cloud (`create_device_code`,
    device_type='ha_kiosk'), then POSTs the resulting user_code here. This box — which is signed
    into the household account via the add-on — authorizes that code for its own account. The
    tablet then polls Dashie's cloud directly for its own per-device JWT.

    **No credential is returned by this view.** The account JWT never leaves the add-on, and the
    tablet's session token comes from its own poll — so nothing here can leak either one.

    Authed by the HA token the tablet already holds (`requires_auth`), which is what makes this
    LAN-scoped: only a device that HA already trusts can even ask. Gated end-to-end on household
    sharing (add-on checks, and jwt-auth re-checks authoritatively), and jwt-auth restricts the
    operation to `device_type='ha_kiosk'` so this cannot sign a Fire TV or a phone into the
    account.

    Why this exists: the anon-kiosk voice MIRROR (hand-plumbing account state across 5 hops) is
    being retired. A logged-in kiosk gets calendars/chores/photos/settings through the ordinary
    account paths — O(1) instead of O(features).
    """

    url = "/api/dashie/account/authorize"
    name = "api:dashie:account:authorize"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

        user_code = (body or {}).get("user_code") or (body or {}).get("device_code") or ""
        if not user_code:
            return web.json_response({"ok": False, "error": "missing_user_code"}, status=400)

        result, status = await authorize_device(hass, user_code)
        if status == 200 and result.get("success"):
            _LOGGER.info("Kiosk device code authorized into the household account")
            return web.json_response(
                {"ok": True, "account_email": result.get("account_email")}, status=200
            )

        _LOGGER.warning(
            "Kiosk authorize refused (%s): %s", status, result.get("error", "unknown")
        )
        return web.json_response(
            {
                "ok": False,
                "error": result.get("error", "authorize_failed"),
                "message": result.get("message", "Could not authorize this device."),
            },
            status=status if status >= 400 else 400,
        )


def register_voice_views(hass: HomeAssistant) -> None:
    """Register Dashie voice gateway HTTP views."""
    hass.http.register_view(DashieVoiceConverseView())
    hass.http.register_view(DashieVoiceStatusView())
    hass.http.register_view(DashieVoiceSessionView())
    hass.http.register_view(DashieAccountAuthorizeView())
    _LOGGER.info("Registered Dashie voice gateway views")
