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

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .addon_bridge import (
    AddonUnavailable,
    SharingDisabled,
    converse_local,
    get_account_credential,
    get_sharing_status,
    get_voice_config,
)

_LOGGER = logging.getLogger(__name__)

# The voice-conversation brain edge function.
# TODO(config): derive per-environment (staging vs prod) instead of hardcoding.
BRAIN_URL = "https://cwglbtosingboqepsmjk.supabase.co/functions/v1/voice-conversation"


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

        session = async_get_clientsession(hass)
        try:
            async with session.post(
                BRAIN_URL,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {cred}",
                    "apikey": cred,
                },
            ) as resp:
                turn = await resp.json(content_type=None)
                status = 200 if resp.status < 400 else resp.status
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("voice converse → brain failed: %s", err)
            return web.json_response({"ok": False, "error": f"brain_call_failed: {err}"}, status=502)

        await self._maybe_retain(hass, turn, text, endpoint_id, payload)
        return web.json_response(turn, status=status)

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
        return web.json_response({
            "available": bool(status.get("available")),
            "reason": status.get("reason", "unknown"),
        })


def register_voice_views(hass: HomeAssistant) -> None:
    """Register Dashie voice gateway HTTP views."""
    hass.http.register_view(DashieVoiceConverseView())
    hass.http.register_view(DashieVoiceStatusView())
    _LOGGER.info("Registered Dashie voice gateway views")
