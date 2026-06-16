"""Bridge to the Dashie add-on for the account credential.

The account login/JWT lives in the **add-on** (the household account hub). The
integration fetches it to call cloud edge functions (the voice-conversation
"brain") on the account's behalf. Both run inside HA on the hassio network.

Reaching the add-on: we ask the **Supervisor** for the add-on's IP (bypasses
internal-DNS quirks), falling back to a few candidate hostnames. The resolved
base URL is cached once it works.

⚠️ v1 SECURITY = network-trust. The add-on's `/api/internal/account-credential`
is ingress-only (not externally exposed) but is not yet authenticated. Acceptable
for single-household dev; HARDEN before wider use (shared secret / scoped token).
Tracked in tech-debt.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

from aiohttp import ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
ADDON_PORT = 8099
_CREDENTIAL_PATH = "/api/internal/account-credential"

# Fallback addresses if Supervisor discovery is unavailable.
# TODO(config): also allow a config_flow override.
_ADDON_CANDIDATES = (
    "http://local-dashie:8099",
    "http://addon_local_dashie:8099",
)

_REFRESH_SKEW = 120.0
_TIMEOUT = ClientTimeout(total=5)

_cache: dict = {"jwt": None, "exp": 0.0}
_working_base: str | None = None


class AddonUnavailable(Exception):
    """The Dashie add-on / account credential isn't reachable."""


async def _discover_via_supervisor(session) -> str | None:
    """Resolve the add-on's base URL (http://<ip>:8099) via the Supervisor API."""
    if not SUPERVISOR_TOKEN:
        _LOGGER.debug("no SUPERVISOR_TOKEN — skipping add-on discovery")
        return None
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    try:
        async with session.get(f"{SUPERVISOR_URL}/addons", headers=headers, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                _LOGGER.debug("supervisor /addons HTTP %s", resp.status)
                return None
            addons = ((await resp.json()).get("data") or {}).get("addons") or []
        slug = next(
            (a.get("slug") for a in addons
             if a.get("slug") == "dashie"
             or (a.get("slug") or "").endswith("_dashie")
             or a.get("name") == "Dashie Console"),
            None,
        )
        if not slug:
            _LOGGER.debug("dashie add-on not found in supervisor list")
            return None
        async with session.get(f"{SUPERVISOR_URL}/addons/{slug}/info", headers=headers, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            info = (await resp.json()).get("data") or {}
        host = info.get("ip_address") or info.get("hostname")
        return f"http://{host}:{ADDON_PORT}" if host else None
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("supervisor discovery failed: %s", err)
        return None


async def get_account_credential(hass: HomeAssistant) -> str:
    """Return the account JWT used to authenticate brain calls (cached until near expiry)."""
    global _working_base
    now = time.time()
    if _cache["jwt"] and now < _cache["exp"] - _REFRESH_SKEW:
        return _cache["jwt"]

    session = async_get_clientsession(hass)

    if _working_base:
        bases = [_working_base]
    else:
        bases = []
        discovered = await _discover_via_supervisor(session)
        if discovered:
            bases.append(discovered)
        bases.extend(_ADDON_CANDIDATES)

    last_err = "no candidates"
    for base in bases:
        url = f"{base}{_CREDENTIAL_PATH}"
        try:
            async with session.get(url, timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    last_err = f"{base}: HTTP {resp.status}"
                    continue
                data = await resp.json(content_type=None)
        except Exception as err:  # noqa: BLE001
            last_err = f"{base}: {err}"
            continue

        jwt = (data or {}).get("jwt")
        if not jwt:
            last_err = f"{base}: no jwt (add-on not signed in?)"
            continue

        _working_base = base
        _cache["jwt"] = jwt
        _cache["exp"] = _parse_expiry(data.get("jwt_expires_at"), now)
        _LOGGER.info("Account credential fetched from add-on at %s", base)
        return jwt

    _working_base = None
    raise AddonUnavailable(last_err)


def _parse_expiry(iso: str | None, now: float) -> float:
    if not iso:
        return now + 3600.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return now + 3600.0
