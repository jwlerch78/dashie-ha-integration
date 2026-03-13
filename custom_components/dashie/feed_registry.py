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

        # Merge with defaults for missing fields
        existing = self._data["feeds"].get(feed_id, {})
        merged = {**DEFAULT_FEED, **existing, **feed_data}
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
        """Return all feed definitions, annotated with live camera availability."""
        hass = request.app["hass"]
        registry: FeedRegistry = hass.data["dashie"]["feed_registry"]
        feeds = registry.get_feeds()
        for feed in feeds.values():
            if feed.get("stream_source_type", "entity") == "entity":
                entity_id = feed.get("camera_entity_id", "")
                state = hass.states.get(entity_id) if entity_id else None
                feed["available"] = state is not None and state.state != "unavailable"
            else:
                feed["available"] = True
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


def register_feed_registry_views(hass: HomeAssistant) -> None:
    """Register feed registry HTTP views."""
    hass.http.register_view(DashieFeedsListView())
    hass.http.register_view(DashieFeedDeleteView())
    hass.http.register_view(DashieSubscriptionView())
    _LOGGER.info("Registered Dashie feed registry views")
