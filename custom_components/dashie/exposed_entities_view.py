"""Exposed-entities endpoint for Dashie.

Returns the entity IDs the user has curated as "exposed to Assist" (HA Settings →
Voice Assistants → Expose). The Dashie cloud/voice brain uses this to send only the
curated subset of entities as context instead of the whole entity cache — one HA
state-question turn on a real install was shipping ~1250 entities / ~50k tokens to
the brain before this (2026-07-13).

Only reachable via the WebSocket `homeassistant/expose_entity/list` command in core
HA; this view mirrors that over REST so the webapp's REST-only ha-service can read it
through the same proxy it uses for /api/states.

HTTP endpoint:
  GET /api/dashie/exposed_entities
    → { "entity_ids": ["cover.garage_door", ...], "count": N }
"""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

_LOGGER = logging.getLogger(__name__)

# The assistant key HA's own conversation agent exposes against. This is the same
# key the WS `homeassistant/expose_entity/list` result reports as `{"conversation": true}`.
_ASSISTANT = "conversation"


def _area_name_for(entity_id, ent_reg, dev_reg, area_reg):
    """Resolve an entity's area NAME ("Living Room"), or None. An entity's area comes from its own
    registry entry, or — when unset — from its device. Room-awareness build 20260715: the voice
    brain uses this to resolve "turn off the lights" to the device's room."""
    ent = ent_reg.async_get(entity_id)
    if ent is None:
        return None
    area_id = ent.area_id
    if area_id is None and ent.device_id:
        device = dev_reg.async_get(ent.device_id)
        if device is not None:
            area_id = device.area_id
    if area_id is None:
        return None
    area = area_reg.async_get_area(area_id)
    return area.name if area is not None else None


class DashieExposedEntitiesView(HomeAssistantView):
    """Return the entity IDs exposed to HA's conversation assistant."""

    url = "/api/dashie/exposed_entities"
    name = "api:dashie:exposed_entities"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]

        entity_ids = [
            entity_id
            for entity_id in hass.states.async_entity_ids()
            if async_should_expose(hass, _ASSISTANT, entity_id)
        ]

        # Room-awareness (20260715): attach each exposed entity's area NAME so the voice brain can
        # resolve a room-relative command ("turn off the lights") to the device's room. Additive —
        # `areas` maps entity_id → area for those that have one; unmapped entities have no area.
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)
        area_reg = ar.async_get(hass)
        areas = {}
        for entity_id in entity_ids:
            name = _area_name_for(entity_id, ent_reg, dev_reg, area_reg)
            if name:
                areas[entity_id] = name

        return web.json_response(
            {"entity_ids": entity_ids, "count": len(entity_ids), "areas": areas}
        )


def register_exposed_entities_views(hass: HomeAssistant) -> None:
    """Register the exposed-entities HTTP view."""
    hass.http.register_view(DashieExposedEntitiesView())
    _LOGGER.info("Registered Dashie exposed-entities view")
