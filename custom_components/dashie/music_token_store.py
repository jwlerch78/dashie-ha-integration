"""Central Music Assistant token store for Dashie.

Stores the MA JWT token centrally in HA so that multiple tablets
can share it without each needing to go through the MA login flow.

Storage: homeassistant.helpers.storage.Store -> .storage/dashie.music_token
HTTP endpoints:
  GET    /api/dashie/music/token  — retrieve stored token + MA URL
  POST   /api/dashie/music/token  — save token + MA URL (from first device to login)
  DELETE /api/dashie/music/token  — clear the stored token AND URL

⚠️ The DELETE route was undocumented here until 2026-10-11, which is most of why nobody knew it
existed. It is the "clear the poisoned value" operation that D-242's deadlock lacked, and it is kept
deliberately MANUAL: it drops the token as well as the URL, so it forces a fresh Music Assistant
sign-in. Nothing calls it automatically and the probe below must not — withholding a bad URL is
recoverable, deleting a good token is not.

## Why GET probes before it answers (D-242)

A household's HA box changed IP. The stored `ma_url` kept pointing at the old host and could not be
cleared by the user: it lives HERE, not on the tablet, so reinstalling the app just pulled it back —
and it is only replaced on a SUCCESSFUL Music Assistant login, which the stale URL is what breaks.

HA is the right place to notice. It is always on, always on the home LAN, and it holds the value, so
it is a better observer of a household-wide fact than any one device — and unlike a device-side fix,
it reaches tablets that will never be updated. So GET withholds a stored `ma_url` that does not
answer, and the device falls through to deriving one from the HA host it is already talking to.
"""
from __future__ import annotations

import asyncio
import logging
import time

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "dashie.music_token"
STORAGE_VERSION = 1

#: Short, because a device is waiting on this response. A host that is simply gone refuses or
#: times out well inside it; a host that is present answers in milliseconds on a LAN.
PROBE_TIMEOUT_SEC = 2.0

#: Probe results are cached this long, keyed by URL. Without it every GET re-probes, and the
#: Android client polls this endpoint on each Music Assistant auth failure.
PROBE_CACHE_SEC = 60.0

#: url -> (monotonic_deadline, answered)
_probe_cache: dict[str, tuple[float, bool]] = {}


async def async_ma_url_answers(hass: HomeAssistant, url: str) -> bool:
    """True when something answers HTTP at *url*.

    Any status counts, 4xx included: the question is "is a server there", not "is it happy".
    Music Assistant answers its API with 401 for a stale token and that is still a reachable MA.
    Only a failure to connect at all counts as absent.

    Cached for [PROBE_CACHE_SEC]; caching a *negative* is safe because the device's own fallback
    (derive from the HA host) is correct in exactly that case.
    """
    now = time.monotonic()
    cached = _probe_cache.get(url)
    if cached and cached[0] > now:
        return cached[1]

    answered = False
    try:
        session = async_get_clientsession(hass)
        async with asyncio.timeout(PROBE_TIMEOUT_SEC):
            async with session.get(f"{url.rstrip('/')}/") as response:
                answered = True
                _LOGGER.debug("MA url %s answered HTTP %s", url, response.status)
    except (TimeoutError, asyncio.TimeoutError, aiohttp.ClientError, OSError) as err:
        _LOGGER.debug("MA url %s did not answer: %s: %s", url, type(err).__name__, err)

    _probe_cache[url] = (now + PROBE_CACHE_SEC, answered)
    return answered


class MusicTokenStore:
    """Stores the MA JWT token centrally."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        self._data = data or {}

    def get_token(self) -> dict:
        """Return {token, ma_url} or empty dict. Returns token even without ma_url."""
        token = self._data.get("token", "")
        if not token:
            return {}
        return {"token": token, "ma_url": self._data.get("ma_url", "")}

    async def async_save_token(self, token: str, ma_url: str) -> None:
        """Store the MA JWT token and URL."""
        self._data = {"token": token, "ma_url": ma_url}
        await self._store.async_save(self._data)
        _LOGGER.info("Saved MA token centrally (url=%s)", ma_url)


# ── HTTP Views ───────────────────────────────────────────────────


class DashieMusicTokenView(HomeAssistantView):
    """Get or save the central MA token."""

    url = "/api/dashie/music/token"
    name = "api:dashie:music:token"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: MusicTokenStore | None = hass.data.get("dashie", {}).get("music_token_store")
        if store is None:
            return web.json_response({})

        payload = store.get_token()
        ma_url = payload.get("ma_url", "")
        if ma_url and not await async_ma_url_answers(hass, ma_url):
            # Withhold, do not delete: unreachable now is not proof it is wrong forever (HA may
            # have come up before the network, or MA may be restarting), and the token must
            # survive so the household is not forced through a fresh sign-in.
            #
            # Loudness is asymmetric ON PURPOSE. The pass is the common case and runs on every
            # auth failure, so it stays at debug; the withhold is the line somebody will need
            # when a device "forgets" its Music Assistant, so it names the URL and the reason.
            # There is exactly ONE candidate here, so there is no chain to enumerate — the
            # device-side resolver owns that and logs every rung it tried.
            _LOGGER.warning(
                "DROP: withholding stored MA url %s — nothing answered it within %.0fs, so this "
                "device is being told to derive one from the Home Assistant host instead. The "
                "token is kept. If Music Assistant has genuinely moved, sign in to it once from a "
                "Dashie device to republish the new address.",
                ma_url,
                PROBE_TIMEOUT_SEC,
            )
            payload = {**payload, "ma_url": ""}
        return web.json_response(payload)

    async def delete(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: MusicTokenStore | None = hass.data.get("dashie", {}).get("music_token_store")
        if store is None:
            return web.json_response({"error": "Store not initialized"}, status=500)
        store._data = {}
        await store._store.async_save({})
        _LOGGER.info("Cleared central MA token")
        return web.json_response({"cleared": True})

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        store: MusicTokenStore | None = hass.data.get("dashie", {}).get("music_token_store")
        if store is None:
            return web.json_response({"error": "Store not initialized"}, status=500)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        token = body.get("token", "")
        ma_url = body.get("ma_url", "")
        if not token or not ma_url:
            return web.json_response({"error": "token and ma_url required"}, status=400)

        await store.async_save_token(token, ma_url)
        return web.json_response({"saved": True})


def register_music_token_views(hass: HomeAssistant) -> None:
    """Register music token HTTP views."""
    hass.http.register_view(DashieMusicTokenView())
    _LOGGER.info("Registered Dashie music token views")
