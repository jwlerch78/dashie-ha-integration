"""Centralized Video Feed Registry for Dashie.

Manages household-level feed definitions and per-device subscriptions.
Feeds are defined once (camera, triggers, stream settings) and tablets
subscribe with a mode: subscribed, trigger, trigger_alert, or ignored.

Storage: homeassistant.helpers.storage.Store -> .storage/dashie.video_feeds
HTTP endpoints for CRUD from any tablet's Settings UI.
"""
from __future__ import annotations

import logging
import time

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "dashie.video_feeds"
STORAGE_VERSION = 1

# Valid subscription modes (order matters for UI display)
SUBSCRIPTION_MODES = ("subscribed", "trigger", "trigger_alert", "ignored")
DEFAULT_MODE = "subscribed"

# Default feed values
DEFAULT_FEED = {
    "stream_source_type": "entity",
    "stream_source_url": "",
    "fps": 10,
    "quality": 8,
    "resolution": 480,
    "triggers": [],
    "auto_dismiss_seconds": 30,
    "continue_while_active": True,
    "alert_sound": "notify_bright_ping",
    "default_mode": DEFAULT_MODE,
    # Frigate camera override:
    #   ""          → auto-detect via _annotate_frigate_camera matcher
    #   "<name>"    → force this Frigate camera, skip matcher
    #   "__none__"  → explicit opt-out; never treat as Frigate
    "frigate_camera_override": "",
}


class FeedRegistry:
    """Manages feed definitions and device subscriptions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict | None = None

    async def async_load(self) -> None:
        """Load data from storage."""
        self._data = await self._store.async_load() or {
            "feeds": {},
            "subscriptions": {},
        }

    async def _async_save(self) -> None:
        """Save data to storage."""
        await self._store.async_save(self._data)

    # ── Feed CRUD ────────────────────────────────────────────────

    @callback
    def get_feeds(self) -> dict[str, dict]:
        """Return all feed definitions."""
        return dict(self._data["feeds"])

    @callback
    def get_feed(self, feed_id: str) -> dict | None:
        """Return a single feed definition."""
        return self._data["feeds"].get(feed_id)

    # Fields that should NOT be silently overwritten with an empty string on
    # an update. A client sending an incomplete payload (e.g. the JS settings
    # auto-save firing with a partially-initialised draft) would otherwise
    # blank out a working association. On a CREATE these fields can still be
    # empty — only existing feeds with a non-empty stored value are protected.
    _PROTECTED_UPDATE_FIELDS: tuple[str, ...] = (
        "camera_entity_id",
        "stream_source_url",
    )

    async def async_create_or_update_feed(self, feed_data: dict) -> dict:
        """Create or update a feed definition."""
        feed_id = feed_data.get("id")
        if not feed_id:
            # Generate ID from label
            label = feed_data.get("label", "feed")
            feed_id = label.lower().replace(" ", "_").replace("-", "_")
            # Ensure unique
            base_id = feed_id
            counter = 2
            while feed_id in self._data["feeds"]:
                feed_id = f"{base_id}_{counter}"
                counter += 1
            feed_data["id"] = feed_id

        # Guard against clients wiping critical fields by sending empty
        # strings. If this is an update (existing feed) and the incoming
        # payload has an empty value for a protected field that already has
        # a non-empty stored value, drop it from the update so the merge
        # preserves the existing value. Loud log so we can trace offending
        # clients.
        existing = self._data["feeds"].get(feed_id, {})
        sanitized_data = dict(feed_data)
        if existing:
            for field in self._PROTECTED_UPDATE_FIELDS:
                incoming = sanitized_data.get(field)
                stored = existing.get(field)
                if incoming == "" and stored not in (None, ""):
                    _LOGGER.warning(
                        "Feed %s: dropping empty '%s' from update (would have "
                        "overwritten stored value %r). Client likely sent an "
                        "incomplete payload; preserving existing value.",
                        feed_id, field, stored,
                    )
                    sanitized_data.pop(field)

        # Merge with defaults for missing fields
        merged = {**DEFAULT_FEED, **existing, **sanitized_data}
        merged["id"] = feed_id
        merged["updated_at"] = time.time()
        if "created_at" not in merged:
            merged["created_at"] = merged["updated_at"]

        self._data["feeds"][feed_id] = merged
        await self._async_save()
        _LOGGER.info("Feed saved: %s (%s)", feed_id, merged.get("label"))
        return merged

    async def async_delete_feed(self, feed_id: str) -> bool:
        """Delete a feed definition."""
        if feed_id not in self._data["feeds"]:
            return False
        del self._data["feeds"][feed_id]
        # Clean up feed from all subscriptions
        for sub in self._data["subscriptions"].values():
            sub.get("feed_modes", {}).pop(feed_id, None)
        await self._async_save()
        _LOGGER.info("Feed deleted: %s", feed_id)
        return True

    # ── Subscription CRUD ────────────────────────────────────────

    @callback
    def get_subscription(self, device_id: str) -> dict:
        """Get a device's subscription (with defaults for new feeds)."""
        sub = self._data["subscriptions"].get(device_id, {
            "device_id": device_id,
            "feed_modes": {},
            "display": {"layout": "grid", "size": "medium", "location": "sidebar"},
        })
        # Fill in default modes for feeds the device hasn't seen
        feed_modes = sub.get("feed_modes", {})
        for feed_id, feed in self._data["feeds"].items():
            if feed_id not in feed_modes:
                feed_modes[feed_id] = feed.get("default_mode", DEFAULT_MODE)
        sub["feed_modes"] = feed_modes
        return sub

    async def async_update_subscription(self, device_id: str, sub_data: dict) -> dict:
        """Update a device's subscription modes and display settings."""
        existing = self._data["subscriptions"].get(device_id, {
            "device_id": device_id,
            "feed_modes": {},
            "display": {},
        })

        # Update feed modes (validate values)
        if "feed_modes" in sub_data:
            for fid, mode in sub_data["feed_modes"].items():
                if mode in SUBSCRIPTION_MODES:
                    existing.setdefault("feed_modes", {})[fid] = mode

        # Update display settings
        if "display" in sub_data:
            existing.setdefault("display", {}).update(sub_data["display"])

        existing["device_id"] = device_id
        self._data["subscriptions"][device_id] = existing
        await self._async_save()
        _LOGGER.debug("Subscription updated for device %s", device_id)
        return self.get_subscription(device_id)

    async def async_remove_subscription(self, device_id: str) -> bool:
        """Remove a device's subscription (e.g. when device is deleted)."""
        if device_id not in self._data["subscriptions"]:
            return False
        del self._data["subscriptions"][device_id]
        await self._async_save()
        _LOGGER.info("Subscription removed for deleted device %s", device_id)
        return True

    # ── Trigger Helpers ──────────────────────────────────────────

    @callback
    def get_all_trigger_entities(self) -> set[str]:
        """Get the set of all trigger entity IDs across all feeds."""
        entities = set()
        for feed in self._data["feeds"].values():
            for trigger in feed.get("triggers", []):
                entities.add(trigger["entity_id"])
        return entities

    @callback
    def get_feeds_tracking_entity(self, entity_id: str) -> list[dict]:
        """Get feeds that reference this entity as a trigger, regardless of state.

        Used to push state=off transitions to devices so continue-while-active
        tracking on the client knows when to stop extending auto-dismiss.
        Matching on state_val alone (as get_feeds_for_trigger does) excludes
        the off transitions, which meant clients cached state=on forever.
        """
        matches = []
        for feed in self._data["feeds"].values():
            for trigger in feed.get("triggers", []):
                if trigger["entity_id"] == entity_id:
                    matches.append(feed)
                    break
        return matches

    @callback
    def get_feeds_for_trigger(self, entity_id: str, state: str) -> list[dict]:
        """Get feeds that match a trigger entity + state."""
        matches = []
        for feed in self._data["feeds"].values():
            for trigger in feed.get("triggers", []):
                if trigger["entity_id"] == entity_id and trigger.get("state") == state:
                    matches.append(feed)
                    break
        return matches

    @callback
    def get_subscribed_devices_for_feed(
        self, feed_id: str, modes: tuple[str, ...] = ("trigger", "trigger_alert")
    ) -> list[tuple[str, str]]:
        """Get (device_id, mode) pairs subscribed to a feed with given modes.

        Also includes devices that haven't explicitly set a mode if the
        feed's default_mode is in the requested modes.
        """
        feed = self._data["feeds"].get(feed_id)
        if not feed:
            return []

        result = []
        default = feed.get("default_mode", DEFAULT_MODE)

        # Check all known devices (from subscriptions + coordinators)
        from .const import DOMAIN
        all_device_ids = set(self._data["subscriptions"].keys())
        # Also include any device that has a coordinator (may not have subscription yet)
        for entry_data in self.hass.data.get(DOMAIN, {}).values():
            if hasattr(entry_data, "device_id") and entry_data.device_id:
                all_device_ids.add(entry_data.device_id)

        for device_id in all_device_ids:
            sub = self._data["subscriptions"].get(device_id, {})
            mode = sub.get("feed_modes", {}).get(feed_id, default)
            if mode in modes:
                result.append((device_id, mode))

        return result


# ── HTTP Views ───────────────────────────────────────────────────


class DashieFeedsListView(HomeAssistantView):
    """List all feeds or create/update a feed."""

    url = "/api/dashie/feeds"
    name = "api:dashie:feeds"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Return all feed definitions, annotated with live camera availability and RTSP URLs."""
        from .stream_proxy import _get_stream_source
        from .frigate_proxy import _detect_frigate, _get_session, _TIMEOUT

        hass = request.app["hass"]
        registry: FeedRegistry = hass.data["dashie"]["feed_registry"]
        feeds = registry.get_feeds()

        # Fetch Frigate camera names (cached after first call)
        frigate_cameras = await _get_frigate_camera_names()

        for feed in feeds.values():
            source_type = feed.get("stream_source_type", "entity")
            if source_type == "entity":
                entity_id = feed.get("camera_entity_id", "")
                state = hass.states.get(entity_id) if entity_id else None
                feed["available"] = state is not None and state.state != "unavailable"
                # Resolve RTSP URL so tablets can connect directly via ExoPlayer
                if feed["available"] and entity_id:
                    feed["rtsp_url"] = await _get_stream_source(hass, entity_id) or ""
                else:
                    feed["rtsp_url"] = ""
            else:
                feed["available"] = True
                feed["rtsp_url"] = feed.get("stream_source_url", "")

            # Annotate with Frigate camera info
            _annotate_frigate_camera(feed, frigate_cameras)

        return web.json_response({"feeds": feeds})

    async def post(self, request: web.Request) -> web.Response:
        """Create or update a feed."""
        registry: FeedRegistry = request.app["hass"].data["dashie"]["feed_registry"]
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        feed = await registry.async_create_or_update_feed(body)
        # Notify trigger system to refresh subscriptions
        _notify_trigger_refresh(request.app["hass"])
        return web.json_response({"feed": feed})


class DashieFeedDeleteView(HomeAssistantView):
    """Delete a feed."""

    url = "/api/dashie/feeds/{feed_id}"
    name = "api:dashie:feeds:delete"
    requires_auth = True

    async def delete(self, request: web.Request, feed_id: str) -> web.Response:
        """Delete a feed definition."""
        registry: FeedRegistry = request.app["hass"].data["dashie"]["feed_registry"]
        if await registry.async_delete_feed(feed_id):
            _notify_trigger_refresh(request.app["hass"])
            return web.json_response({"deleted": feed_id})
        return web.json_response({"error": "Feed not found"}, status=404)


class DashieSubscriptionView(HomeAssistantView):
    """Get or update a device's feed subscriptions."""

    url = "/api/dashie/feeds/subscriptions/{device_id}"
    name = "api:dashie:feeds:subscriptions"
    requires_auth = True

    async def get(self, request: web.Request, device_id: str) -> web.Response:
        """Return a device's subscription."""
        registry: FeedRegistry = request.app["hass"].data["dashie"]["feed_registry"]
        return web.json_response(registry.get_subscription(device_id))

    async def post(self, request: web.Request, device_id: str) -> web.Response:
        """Update a device's subscription."""
        registry: FeedRegistry = request.app["hass"].data["dashie"]["feed_registry"]
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        sub = await registry.async_update_subscription(device_id, body)
        return web.json_response(sub)


@callback
def _notify_trigger_refresh(hass: HomeAssistant) -> None:
    """Signal coordinators to refresh trigger subscriptions."""
    from .const import DOMAIN
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if hasattr(entry_data, "refresh_feed_triggers"):
            entry_data.refresh_feed_triggers()


# ── Frigate Camera Detection ────────────────────────────────────

# Cache of Frigate camera names (refreshed when None)
_frigate_camera_cache: list[str] | None = None
_frigate_cache_time: float = 0
# Long TTL for successful (non-empty) camera list — cameras don't change often.
_FRIGATE_CACHE_TTL = 300  # 5 minutes
# Short TTL when the last probe returned empty — avoids being stuck at an empty
# list for 5 minutes when Frigate was briefly unreachable (container restart,
# URL stale, etc.). Previously the fix required a HA restart to clear.
_FRIGATE_EMPTY_CACHE_TTL = 30  # 30 seconds


async def _get_frigate_camera_names() -> list[str]:
    """Fetch camera names from Frigate, with caching."""
    global _frigate_camera_cache, _frigate_cache_time
    from .frigate_proxy import _detect_frigate, _get_session, _TIMEOUT

    now = time.time()
    if _frigate_camera_cache is not None:
        age = now - _frigate_cache_time
        ttl = _FRIGATE_CACHE_TTL if _frigate_camera_cache else _FRIGATE_EMPTY_CACHE_TTL
        if age < ttl:
            return _frigate_camera_cache

    base = await _detect_frigate()
    if not base:
        _LOGGER.info("Frigate not detected — no Frigate annotations will be applied to feeds")
        # Cache empty result briefly so we don't probe on every feed list call,
        # but short enough to self-heal once Frigate is reachable again.
        _frigate_camera_cache = []
        _frigate_cache_time = now
        return []

    try:
        session = await _get_session()
        async with session.get(f"{base}/api/config", timeout=_TIMEOUT) as resp:
            if resp.status == 200:
                config = await resp.json()
                cameras = list(config.get("cameras", {}).keys())
                _frigate_camera_cache = cameras
                _frigate_cache_time = now
                _LOGGER.info("Frigate cameras refreshed: %s", cameras)
                return cameras
            else:
                _LOGGER.warning("Frigate /api/config returned %s (base=%s)", resp.status, base)
    except Exception as err:
        _LOGGER.warning("Failed to fetch Frigate cameras from %s: %s — will retry", base, err)
        # URL might be stale (container moved). Invalidate the cached URL so
        # the next call re-probes all candidates.
        from . import frigate_proxy as _fp
        _fp._frigate_url = None

    _frigate_camera_cache = []
    _frigate_cache_time = now
    return []


def _annotate_frigate_camera(feed: dict, frigate_cameras: list[str]) -> None:
    """Annotate a feed dict with Frigate camera info if it matches.

    When `frigate_cameras` is empty (Frigate not reachable), we PRESERVE the
    feed's existing is_frigate_camera / frigate_camera_name so a transient
    Frigate outage doesn't clobber previously-established Frigate annotations.
    When `frigate_cameras` is non-empty and the feed claims a Frigate camera
    name that's NOT in the current list, we reset — that stored name is
    stale (e.g., the camera was removed from Frigate config since last sync).
    User-visible impact otherwise: the playback/clips icon disappears from
    every Frigate-connected feed whenever Frigate has a brief hiccup, OR
    appears on feeds whose Frigate camera was long since removed.
    """
    if not frigate_cameras:
        feed.setdefault("is_frigate_camera", False)
        feed.setdefault("frigate_camera_name", "")
        return

    # User override takes precedence over auto-detection.
    override = feed.get("frigate_camera_override", "")
    if override == "__none__":
        feed["is_frigate_camera"] = False
        feed["frigate_camera_name"] = ""
        return
    if override and override in frigate_cameras:
        feed["is_frigate_camera"] = True
        feed["frigate_camera_name"] = override
        return
    if override and override not in frigate_cameras:
        # Override points at a camera that no longer exists in Frigate config.
        # Fall through to auto-match instead of leaving the feed broken.
        _LOGGER.info("Feed '%s' override=%s no longer in Frigate cameras %s — falling back to auto-match",
                     feed.get("label"), override, frigate_cameras)

    # Frigate is reachable — validate any existing annotation against the
    # current camera list. If the stored name isn't in the list, clear it
    # so the match loop below can re-evaluate (and correctly leave it false
    # if no match).
    stored_name = feed.get("frigate_camera_name", "")
    if stored_name and stored_name not in frigate_cameras:
        _LOGGER.info("Feed '%s' had stale Frigate annotation (name=%s not in %s) — clearing",
                     feed.get("label"), stored_name, frigate_cameras)
        feed["is_frigate_camera"] = False
        feed["frigate_camera_name"] = ""

    stream_url = feed.get("stream_source_url", "")
    source_type = feed.get("stream_source_type", "")
    label = feed.get("label", "").lower().replace(" ", "_")
    entity_id = feed.get("camera_entity_id", "").lower()
    feed_id = feed.get("id", "").lower()

    for cam_name in frigate_cameras:
        # Match by go2rtc stream name
        if source_type == "go2rtc" and (
            stream_url == cam_name
            or stream_url.startswith(f"{cam_name}_")
        ):
            feed["is_frigate_camera"] = True
            feed["frigate_camera_name"] = cam_name
            return

        # Match by feed label (e.g., "Family Room" → "family_room")
        if label == cam_name or label.replace(" ", "_") == cam_name:
            feed["is_frigate_camera"] = True
            feed["frigate_camera_name"] = cam_name
            return

        # Match by feed ID (e.g., "family_room")
        if feed_id == cam_name:
            feed["is_frigate_camera"] = True
            feed["frigate_camera_name"] = cam_name
            return

        # Match by camera entity ID containing the camera name
        # e.g., "camera.family_room_camera_hd_stream" contains "family_room"
        if entity_id and cam_name in entity_id:
            feed["is_frigate_camera"] = True
            feed["frigate_camera_name"] = cam_name
            return

    feed["is_frigate_camera"] = False
    feed["frigate_camera_name"] = ""
    _LOGGER.info("Feed '%s' did not match any Frigate camera (label=%s, entity=%s, id=%s, frigate_cams=%s)",
                 feed.get("label"), label, entity_id, feed_id, frigate_cameras)


def register_feed_registry_views(hass: HomeAssistant) -> None:
    """Register feed registry HTTP views."""
    hass.http.register_view(DashieFeedsListView())
    hass.http.register_view(DashieFeedDeleteView())
    hass.http.register_view(DashieSubscriptionView())
    _LOGGER.info("Registered Dashie feed registry views")
