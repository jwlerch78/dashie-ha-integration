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
    → { "entity_ids": ["cover.garage_door", ...], "count": N, "areas": {...},
        "entities": [{entity_id, domain, friendly_name, state, area?, aliases?}, ...] }

`entities` is the ENRICHED brain-shaped list (+ aliases from the HA expose UI) — the single source
both the voice brain and the on-device fast-path read. See
.reference/build-plans/20260717_HA_ENTITY_EXPOSURE_CONTRACT.md. `entity_ids`/`areas` kept for
back-compat. Same shape is returned by /api/dashie/dashboard_entities.
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


def _enrich_entities(entity_ids, hass, ent_reg, dev_reg, area_reg):
    """Brain-shaped entity objects for a set of ids — the SINGLE enriched list the voice brain and
    the on-device fast-path both read (see .reference/build-plans/20260717_HA_ENTITY_EXPOSURE_CONTRACT.md).

    Shape: {entity_id, domain, friendly_name, state, area?, aliases?} — identical to JS
    entitiesForBrain() output, PLUS `aliases` (the HA "Expose to Assist" Aliases column, from the
    entity registry). `area`/`aliases` are omitted when empty. Ids with no current state are skipped
    (stale/removed refs)."""
    out = []
    debug_err = None
    for entity_id in entity_ids:
        try:
            st = hass.states.get(entity_id)
            if st is None:
                continue
            ent = {
                "entity_id": entity_id,
                "domain": entity_id.split(".")[0],
                "friendly_name": st.attributes.get("friendly_name") or "",
                "state": st.state,
            }
            area = _area_name_for(entity_id, ent_reg, dev_reg, area_reg)
            if area:
                ent["area"] = area
            reg = ent_reg.async_get(entity_id)
            raw_aliases = getattr(reg, "aliases", None) if reg is not None else None
            if raw_aliases:
                # Keep only real string aliases. reg.aliases can contain non-str objects (HA's
                # ComputedNameType) that HA's own JSON encoder handles but web.json_response's
                # stdlib json.dumps cannot — those must be dropped or the endpoint 500s.
                str_aliases = sorted(a for a in raw_aliases if isinstance(a, str) and a)
                if str_aliases:
                    ent["aliases"] = str_aliases
            out.append(ent)
        except Exception as e:  # noqa: BLE001 — one bad entity must never 500 the endpoint
            if debug_err is None:
                debug_err = f"{entity_id}: {type(e).__name__}: {e}"
            continue
    return out, debug_err


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
        area_err = None
        for entity_id in entity_ids:
            try:
                name = _area_name_for(entity_id, ent_reg, dev_reg, area_reg)
                if name:
                    areas[entity_id] = name
            except Exception as e:  # noqa: BLE001
                if area_err is None:
                    area_err = f"area {entity_id}: {type(e).__name__}: {e}"

        entities, enrich_err = _enrich_entities(entity_ids, hass, ent_reg, dev_reg, area_reg)
        resp = {"entity_ids": entity_ids, "count": len(entity_ids), "areas": areas, "entities": entities}
        if area_err or enrich_err:
            resp["_debug_err"] = area_err or enrich_err
        return web.json_response(resp)


# Lovelace card fields that reference entities. Kept small + explicit — the common cards use these;
# a recursive walk over the whole config catches nested stacks/grids too.
def _walk_entities(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("entity", "entity_id") and isinstance(v, str) and "." in v:
                out.add(v)
            elif k == "entities" and isinstance(v, list):
                for e in v:
                    if isinstance(e, str) and "." in e:
                        out.add(e)
                    elif isinstance(e, dict):
                        _walk_entities(e, out)
            else:
                _walk_entities(v, out)
    elif isinstance(obj, list):
        for x in obj:
            _walk_entities(x, out)


def _lovelace_dashboards(hass: HomeAssistant):
    """The url_path → LovelaceConfig map, across HA-version shapes of hass.data['lovelace'].
    Newer HA: a LovelaceData dataclass with `.dashboards`; older: a plain dict."""
    data = hass.data.get("lovelace")
    if data is None:
        return {}
    dashboards = getattr(data, "dashboards", None)
    if dashboards is not None:
        return dashboards
    return data if isinstance(data, dict) else {}


def _url_path_from_start_url(start_url: str):
    """Parse a Lovelace dashboard url_path out of a device's startUrl. HA dashboard URLs are
    <base>/<url_path>/<view> for a named dashboard or <base>/lovelace/<view> for the default.
    Returns the url_path, or None for the default dashboard (which the config map keys under None)."""
    if not start_url:
        return None
    from urllib.parse import urlparse

    path = urlparse(start_url).path if "://" in start_url else start_url
    segs = [s for s in path.split("/") if s]
    if not segs or segs[0] == "lovelace":
        return None
    return segs[0]


def _start_url_for_device(hass: HomeAssistant, device_id: str):
    """The startUrl (dashboard the device loads) for a Dashie device, from its coordinator."""
    from .const import DOMAIN

    for coord in hass.data.get(DOMAIN, {}).values():
        if getattr(coord, "device_id", None) == device_id:
            data = getattr(coord, "data", None) or {}
            return data.get("startUrl", "")
    return ""


class DashieDashboardEntitiesView(HomeAssistantView):
    """Return the entity IDs referenced by a Lovelace dashboard (the plug-and-play voice inclusion
    list — "what's on my screen is controllable"). The tablet passes the `url_path` of the dashboard
    it displays; default (omitted) = HA's default dashboard. In-process access to hass.data['lovelace']
    bypasses the REST /api/lovelace/config 404. Additive; returns the same {entity_ids, areas} shape
    as exposed_entities so the caller treats both sources identically. Room-awareness build 20260715.
    """

    url = "/api/dashie/dashboard_entities"
    name = "api:dashie:dashboard_entities"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        # Prefer an explicit url_path; else resolve the DEVICE's dashboard from its startUrl (keyed
        # by StableDeviceId, like /device/area). Either way we end at a Lovelace url_path.
        url_path = request.query.get("url_path", "").strip() or None
        device_id = request.query.get("device_id", "").strip()
        if url_path is None and device_id:
            url_path = _url_path_from_start_url(_start_url_for_device(hass, device_id))

        dashboards = _lovelace_dashboards(hass)
        board = dashboards.get(url_path)
        if board is None:
            return web.json_response(
                {"entity_ids": [], "count": 0, "areas": {}, "error": "dashboard_not_found", "url_path": url_path}
            )

        try:
            config = await board.async_load(False)
        except Exception as err:  # noqa: BLE001 — any load failure → empty, never 500 the picker
            _LOGGER.debug("dashboard_entities: async_load failed for %s: %s", url_path, err)
            return web.json_response(
                {"entity_ids": [], "count": 0, "areas": {}, "error": "config_load_failed", "url_path": url_path}
            )

        found: set[str] = set()
        _walk_entities(config or {}, found)
        # Only entities that actually exist as states (drop stale/removed refs and non-entity dotted
        # strings that slipped through), then attach areas via the shared resolver.
        entity_ids = [e for e in sorted(found) if hass.states.get(e) is not None]
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)
        area_reg = ar.async_get(hass)
        areas = {}
        for entity_id in entity_ids:
            name = _area_name_for(entity_id, ent_reg, dev_reg, area_reg)
            if name:
                areas[entity_id] = name

        entities, enrich_err = _enrich_entities(entity_ids, hass, ent_reg, dev_reg, area_reg)
        resp = {"entity_ids": entity_ids, "count": len(entity_ids), "areas": areas,
                "entities": entities, "url_path": url_path}
        if enrich_err:
            resp["_debug_err"] = enrich_err
        return web.json_response(resp)


def register_exposed_entities_views(hass: HomeAssistant) -> None:
    """Register the exposed-entities HTTP views."""
    hass.http.register_view(DashieExposedEntitiesView())
    hass.http.register_view(DashieDashboardEntitiesView())
    _LOGGER.info("Registered Dashie exposed-entities + dashboard-entities views")
