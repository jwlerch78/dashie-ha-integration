"""Dashie conversation agent — exposes the Dashie voice brain to HA Assist.

Any HA voice satellite (Voice PE, kiosk-satellite / Voice Satellite, the Assist mobile app, a
browser) whose Assist pipeline selects "Dashie" as its conversation agent routes its utterance
here. Unlike DashieVoiceConverseView — which returns the turn to a Dashie *device* that executes
HA actions natively — a third-party satellite has no Dashie runtime, so this agent EXECUTES any
HA action in-process via hass.services and returns only the spoken text.

Headless caller: we send client_fulfilled_tools=[] so the brain self-fulfills what it can
server-side (weather/sports/web/time/help + HA device control) and declines the tablet-only
tools (calendar/music/video/schedule) with a spoken line. Room awareness: the satellite's HA
device area (from user_input.device_id) is sent as device_area so "turn off the lights" resolves
to the satellite's room.

Build plan: dashieapp_staging .reference/build-plans/20260723_VOICE_SATELLITE_OPEN_CORE.md (WS-N).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    intent,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

try:
    from homeassistant.components.homeassistant.exposed_entities import async_should_expose
except Exception:  # noqa: BLE001 — stable helper; guard so a rename never breaks the platform
    async_should_expose = None

try:
    _CONTROL_FEATURE = conversation.ConversationEntityFeature.CONTROL
except AttributeError:  # older HA without the feature flag
    _CONTROL_FEATURE = 0

from .addon_bridge import AddonUnavailable, SharingDisabled
from .const import DOMAIN
from .exposed_entities_view import _enrich_entities
from .voice_view import build_brain_payload, call_cloud_brain

_LOGGER = logging.getLogger(__name__)

_CONVERSATION_ADDED = "_conversation_added"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add ONE household-wide Dashie conversation agent (guarded across config entries).

    Spike scope: the agent attaches to the first Dashie config entry that loads. If that entry is
    later removed the agent unloads — productionizing it (a dedicated household entry independent
    of any tablet) is deferred to WS-N.
    """
    data = hass.data.setdefault(DOMAIN, {})
    if data.get(_CONVERSATION_ADDED):
        return
    data[_CONVERSATION_ADDED] = True
    async_add_entities([DashieConversationEntity()])


class DashieConversationEntity(conversation.ConversationEntity):
    """Routes an Assist utterance to the Dashie brain and executes HA actions in-process."""

    _attr_has_entity_name = False
    _attr_name = "Dashie"
    _attr_unique_id = f"{DOMAIN}_conversation"
    _attr_supported_features = _CONTROL_FEATURE

    @property
    def supported_languages(self) -> list[str] | str:
        # The brain handles language; accept everything.
        return MATCH_ALL

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        hass = self.hass
        text = (user_input.text or "").strip()
        language = user_input.language or "en"

        if not text:
            return self._result("Sorry, I didn't catch that.", user_input, language)

        # Build the headless VoiceRequest: exposed entities + the satellite's room.
        provided_context: dict[str, Any] = {}
        entities = _gather_exposed_entities(hass)
        if entities:
            provided_context["ha_entities"] = entities
        device_area = _device_area_name(hass, getattr(user_input, "device_id", None))
        if device_area:
            provided_context["device_area"] = device_area

        body = {
            "text": text,
            "conversation_id": user_input.conversation_id,
            "client_fulfilled_tools": [],  # headless satellite — brain self-fulfills or declines
            "provided_context": provided_context,
            "timezone": str(hass.config.time_zone or "UTC"),
        }
        payload, _endpoint_id, _route = build_brain_payload(body)

        try:
            turn, status = await call_cloud_brain(hass, payload)
        except SharingDisabled:
            _LOGGER.warning("DROP: Dashie conversation — household sharing is OFF")
            return self._result("Dashie Cloud sharing is turned off for this home.", user_input, language)
        except AddonUnavailable as err:
            _LOGGER.warning("DROP: Dashie conversation — add-on unavailable: %s", err)
            return self._result("I can't reach Dashie right now. Please try again.", user_input, language)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("DROP: Dashie conversation — brain call failed: %s", err)
            return self._result("Something went wrong reaching Dashie.", user_input, language)

        if status >= 400 or not isinstance(turn, dict):
            _LOGGER.warning("DROP: Dashie brain returned HTTP %s: %s", status, turn)
            return self._result("Dashie couldn't handle that request.", user_input, language)

        # Execute any HA action(s) the brain resolved, in-process (the satellite can't).
        await self._execute_ha_actions(hass, turn)

        speech = turn.get("voice") or turn.get("text") or ""

        # Tools that can't be fulfilled headless (calendar/music/video/schedule) come back as an
        # unsupported_tool marker or a client_tool request — the brain usually still gives a spoken
        # decline, but log a loud DROP either way (standing rule #2) and never go silent.
        if turn.get("unsupported_tool"):
            _LOGGER.warning("DROP: brain routed to unsupported_tool=%s (headless satellite)", turn["unsupported_tool"])
        client_tool = turn.get("client_tool")
        if isinstance(client_tool, dict) and client_tool.get("tool"):
            _LOGGER.warning("DROP: brain returned client_tool=%s (not fulfillable headless)", client_tool["tool"])
            if not speech:
                speech = "That isn't available on this device."

        return self._result(speech or "OK.", user_input, language)

    async def _execute_ha_actions(self, hass: HomeAssistant, turn: dict) -> None:
        """Run any home_assistant execute_commands the brain returned (single 'action' or 'multi' steps)."""
        actions: list[dict] = []
        if isinstance(turn.get("action"), dict):
            actions.append(turn["action"])
        for step in turn.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if isinstance(step.get("action"), dict):
                actions.append(step["action"])
            elif step.get("client_tool"):
                _LOGGER.warning("DROP: multi step client_tool=%s (headless)", step.get("client_tool"))

        for act in actions:
            category = act.get("category")
            command = act.get("command")
            if category not in ("homeassistant", "home_assistant"):
                _LOGGER.warning("DROP: non-HA action category=%s command=%s", category, command)
                continue
            if command != "execute_commands":
                _LOGGER.warning("DROP: unknown HA action command=%s", command)
                continue
            for cmd in (act.get("parameters") or {}).get("commands") or []:
                domain = cmd.get("domain")
                service = cmd.get("service")
                if not domain or not service:
                    _LOGGER.warning("DROP: HA command missing domain/service: %s", cmd)
                    continue
                # The brain emits {domain, service, data:{...}} — service data NESTED under
                # "data" (e.g. {"entity_id": "switch.string_lights"}). Tolerate a flat
                # {domain, service, ...data} shape too for forward-compat.
                if isinstance(cmd.get("data"), dict):
                    data = dict(cmd["data"])
                else:
                    data = {k: v for k, v in cmd.items() if k not in ("domain", "service")}
                try:
                    await hass.services.async_call(domain, service, data, blocking=True)
                    _LOGGER.info("Dashie executed HA %s.%s %s", domain, service, data)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("DROP: HA %s.%s failed: %s", domain, service, err)

    @staticmethod
    def _result(
        speech: str, user_input: conversation.ConversationInput, language: str
    ) -> conversation.ConversationResult:
        intent_response = intent.IntentResponse(language=language)
        intent_response.async_set_speech(speech)
        return conversation.ConversationResult(
            response=intent_response, conversation_id=user_input.conversation_id
        )


def _gather_exposed_entities(hass: HomeAssistant) -> list[dict]:
    """The Assist-exposed entity set, brain-shaped (reuses the exposed_entities_view enricher)."""
    if async_should_expose is None:
        return []
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)
    entity_ids = [
        eid for eid in hass.states.async_entity_ids()
        if async_should_expose(hass, "conversation", eid)
    ]
    entities, _err = _enrich_entities(entity_ids, hass, ent_reg, dev_reg, area_reg)
    return entities


def _device_area_name(hass: HomeAssistant, device_id: str | None) -> str | None:
    """The HA area NAME of the satellite that originated this turn (room awareness)."""
    if not device_id:
        return None
    dev = dr.async_get(hass).async_get_device(device_id)
    if dev is None or dev.area_id is None:
        return None
    area = ar.async_get(hass).async_get_area(dev.area_id)
    return area.name if area is not None else None
