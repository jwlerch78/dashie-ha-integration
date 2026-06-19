"""HA-local voice transcript store for Dashie (build plan §17).

When an account opts into transcript retention AND the turn runs in caller mode
(anonymous kiosk via the voice gateway), the cloud brain persists NO transcript
text to Supabase — it only signals `metadata.retain_transcript`. The transcript
then stays on the user's own Home Assistant box, here.

Storage: homeassistant.helpers.storage.Store -> .storage/dashie.voice_transcripts
Capped at the most recent MAX_TRANSCRIPTS turns. Cleared only by the DELETE
endpoint or a full HA wipe.

HTTP endpoints (HA-token authed):
  GET    /api/dashie/voice/transcripts        — recent transcripts (newest first)
  DELETE /api/dashie/voice/transcripts        — clear all stored transcripts
"""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "dashie.voice_transcripts"
STORAGE_VERSION = 1
MAX_TRANSCRIPTS = 500


class TranscriptStore:
    """Append-and-cap store of recent voice transcripts (HA-local)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        self._data = data or {}

    def _items(self) -> list[dict]:
        return self._data.get("transcripts", [])

    async def async_append(
        self,
        *,
        text: str,
        voice: str,
        subtext: str | None,
        endpoint_id: str | None,
        session_id: str | None,
    ) -> None:
        """Append one transcript turn, capping to MAX_TRANSCRIPTS (oldest dropped)."""
        items = self._items()
        items.append(
            {
                "ts": dt_util.utcnow().isoformat(),
                "text": text,
                "voice": voice,
                "subtext": subtext,
                "endpoint_id": endpoint_id,
                "session_id": session_id,
            }
        )
        if len(items) > MAX_TRANSCRIPTS:
            items = items[-MAX_TRANSCRIPTS:]
        self._data = {"transcripts": items}
        await self._store.async_save(self._data)

    def get(self, limit: int = 100) -> list[dict]:
        """Return the most recent transcripts, newest first."""
        items = self._items()
        return list(reversed(items[-limit:]))

    async def async_clear(self) -> int:
        """Delete all stored transcripts. Returns the count removed."""
        count = len(self._items())
        self._data = {"transcripts": []}
        await self._store.async_save(self._data)
        return count


# ── HTTP Views ───────────────────────────────────────────────────


class DashieVoiceTranscriptsView(HomeAssistantView):
    """Read or clear HA-local voice transcripts."""

    url = "/api/dashie/voice/transcripts"
    name = "api:dashie:voice:transcripts"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: TranscriptStore | None = hass.data.get("dashie", {}).get("transcript_store")
        if store is None:
            return web.json_response({"transcripts": []})
        try:
            limit = int(request.query.get("limit", "100"))
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, MAX_TRANSCRIPTS))
        return web.json_response({"transcripts": store.get(limit)})

    async def delete(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: TranscriptStore | None = hass.data.get("dashie", {}).get("transcript_store")
        if store is None:
            return web.json_response({"cleared": 0})
        removed = await store.async_clear()
        return web.json_response({"cleared": removed})


def register_transcript_views(hass: HomeAssistant) -> None:
    """Register Dashie voice transcript HTTP views."""
    hass.http.register_view(DashieVoiceTranscriptsView())
    _LOGGER.info("Registered Dashie voice transcript views")
