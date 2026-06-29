"""Camera discovery for Dashie video feeds.

Endpoint: GET /api/dashie/feeds/discover

Returns cameras the household could add as feeds — every HA ``camera.*``
entity, tagged by whether it's backed by a Frigate camera — minus cameras
already added as feeds, minus cameras that can't actually stream right now.
The Console's "Discover cameras" picker (and, later, the Android settings
page) render this list and POST a chosen camera back to ``/api/dashie/feeds``.

Dedup: a single physical camera often surfaces as several HA entities (hd/sd
substreams) that all map to one Frigate camera. We collapse those into a
single candidate keyed by the Frigate camera name; non-Frigate cameras are
offered one-per-entity.

Streamability gate (v1): only cameras with a resolvable stream source (or a
Frigate route) are offered. A device camera that isn't currently publishing
(no stream source — e.g. a Dashie tablet camera that's turned off) is hidden
until it comes online, so we never suggest a dead/green feed.

Frigate cameras with no HA ``camera.*`` entity are out of scope for v1 (the
common case — Frigate + an upstream camera integration — always creates HA
entities, which this enumerates).
"""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .feed_registry import _get_frigate_camera_names

_LOGGER = logging.getLogger(__name__)


def _match_frigate_camera(
    entity_id: str, friendly_name: str, frigate_cameras: list[str]
) -> str | None:
    """Map a bare camera entity to a Frigate camera name, if any.

    Mirrors the label/entity-substring branches of
    ``feed_registry._annotate_frigate_camera`` but works on a raw entity
    (a discovery candidate isn't a feed yet, so the feed-based
    ``get_frigate_camera_for_entity`` can't be used here).
    """
    eid = entity_id.lower()
    label = (friendly_name or "").lower().replace(" ", "_")
    for cam in frigate_cameras:
        if label == cam or cam in eid:
            return cam
    return None


async def _stream_source_for(hass: HomeAssistant, entity_id: str) -> str | None:
    """Cheap streamability probe — the entity's stream source, or None."""
    from .stream_proxy import _get_stream_source

    try:
        return await _get_stream_source(hass, entity_id)
    except Exception:  # noqa: BLE001 — any failure means "not streamable now"
        return None


class DashieFeedsDiscoverView(HomeAssistantView):
    """GET /api/dashie/feeds/discover — cameras the user could add as feeds."""

    url = "/api/dashie/feeds/discover"
    name = "api:dashie:feeds:discover"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        registry = hass.data.get("dashie", {}).get("feed_registry")

        # Cameras already added as feeds — exclude from discovery.
        existing_entities: set[str] = set()
        existing_frigate: set[str] = set()
        if registry is not None:
            for feed in registry.get_feeds().values():
                if eid := feed.get("camera_entity_id"):
                    existing_entities.add(eid)
                if fc := feed.get("frigate_camera_name"):
                    existing_frigate.add(fc)

        frigate_cameras = await _get_frigate_camera_names()
        cam_states = hass.states.async_all("camera")

        async def _evaluate(state):
            entity_id = state.entity_id
            friendly = state.attributes.get("friendly_name") or entity_id
            frigate = _match_frigate_camera(entity_id, friendly, frigate_cameras)
            source = await _stream_source_for(hass, entity_id)
            return entity_id, friendly, frigate, source

        evaluated = await asyncio.gather(*(_evaluate(s) for s in cam_states))

        # Group/dedup: one candidate per Frigate camera, else per entity.
        # First streamable entity for a key wins as the representative.
        by_key: dict[str, dict] = {}
        for entity_id, friendly, frigate, source in evaluated:
            if entity_id in existing_entities:
                continue
            if frigate and frigate in existing_frigate:
                continue
            # v1 streamability gate: a Frigate route, or a resolvable source.
            if not (frigate or source is not None):
                continue
            key = f"frigate:{frigate}" if frigate else f"entity:{entity_id}"
            if key in by_key:
                continue  # dedup hd/sd substreams of the same camera
            label = (
                frigate.replace("_", " ").title() if frigate else friendly
            )
            by_key[key] = {
                "entity_id": entity_id,
                "label": label,
                "source": "frigate" if frigate else "ha",
                "frigate_camera": frigate or "",
                "snapshot_url": f"/api/dashie/stream/snapshot/{entity_id}",
            }

        candidates = sorted(by_key.values(), key=lambda c: c["label"].lower())
        _LOGGER.debug(
            "Feed discovery: %d candidate(s) from %d camera entit(ies)",
            len(candidates), len(cam_states),
        )
        return web.json_response({"cameras": candidates})


def register_feed_discovery_views(hass: HomeAssistant) -> None:
    """Register the feed discovery HTTP view."""
    hass.http.register_view(DashieFeedsDiscoverView())
    _LOGGER.info("Registered Dashie feed discovery view")
