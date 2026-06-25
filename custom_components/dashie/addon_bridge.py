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
_SHARING_STATUS_PATH = "/api/internal/sharing-status"
_VOICE_CONFIG_PATH = "/api/internal/voice-config"
# On-prem brain (local model, runs IN the add-on — build plan §13.16/§13.17).
_CONVERSE_LOCAL_PATH = "/api/voice/converse-local"

# Fallback addresses if Supervisor discovery is unavailable.
# TODO(config): also allow a config_flow override.
_ADDON_CANDIDATES = (
    "http://local-dashie:8099",
    "http://addon_local_dashie:8099",
)

_REFRESH_SKEW = 120.0
_TIMEOUT = ClientTimeout(total=5)
# Brain calls run model inference on-prem (a LAN model, possibly cold) → far longer than
# the 5s control timeout. Build plan §13.10 measured ~10s cold on a Mac 7B.
_BRAIN_TIMEOUT = ClientTimeout(total=60)

_cache: dict = {"jwt": None, "exp": 0.0}
_working_base: str | None = None


class AddonUnavailable(Exception):
    """The Dashie add-on / account credential isn't reachable."""


class SharingDisabled(AddonUnavailable):
    """Add-on reachable + signed in, but household Dashie Cloud sharing is off.

    Subclasses AddonUnavailable so existing handlers still catch it, while
    callers that care can distinguish "off by choice" from "unreachable".
    """


async def _resolve_bases(session) -> list[str]:
    """Candidate add-on base URLs: the cached working one, else Supervisor
    discovery + the fallback hostnames."""
    if _working_base:
        return [_working_base]
    bases: list[str] = []
    discovered = await _discover_via_supervisor(session)
    if discovered:
        bases.append(discovered)
    bases.extend(_ADDON_CANDIDATES)
    return bases


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
    bases = await _resolve_bases(session)

    last_err = "no candidates"
    for base in bases:
        url = f"{base}{_CREDENTIAL_PATH}"
        try:
            async with session.get(url, timeout=_TIMEOUT) as resp:
                status = resp.status
                data = await resp.json(content_type=None) if status == 200 else None
        except Exception as err:  # noqa: BLE001
            last_err = f"{base}: {err}"
            continue

        # 403 = add-on reachable + signed in but sharing is off. Definitive —
        # raise outside the try so it isn't swallowed, and don't try other bases.
        if status == 403:
            _working_base = base
            raise SharingDisabled("household sharing disabled")
        if status != 200:
            last_err = f"{base}: HTTP {status}"
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


async def get_sharing_status(hass: HomeAssistant) -> dict:
    """Probe the add-on's sharing-status endpoint (capability check, no credential).

    Returns the add-on's `{available, signed_in, household_sharing, reason}` dict,
    or a synthesized `{available: False, reason: "addon_unreachable"}` when the
    add-on can't be reached. Never raises.
    """
    global _working_base
    session = async_get_clientsession(hass)
    bases = await _resolve_bases(session)
    for base in bases:
        try:
            async with session.get(f"{base}{_SHARING_STATUS_PATH}", timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json(content_type=None)
        except Exception:  # noqa: BLE001
            continue
        _working_base = base
        return data or {"available": False, "reason": "bad_response"}
    return {"available": False, "reason": "addon_unreachable"}


async def get_voice_config(hass: HomeAssistant) -> dict:
    """The account's voice ROUTE, read by the add-on from user_settings (build plan §13.17/§16.7).

    Returns the add-on's `{route: 'local'|'cloud', model_is_local: bool}` so the gateway can route
    cloud-vs-local based on the account's selected AI model ("My Local LLM" → local) WITHOUT the
    integration reading Supabase. Defaults to `{route: 'cloud'}` when the add-on is unreachable —
    never raises (the gateway must keep working).
    """
    global _working_base
    session = async_get_clientsession(hass)
    bases = await _resolve_bases(session)
    for base in bases:
        try:
            async with session.get(f"{base}{_VOICE_CONFIG_PATH}", timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json(content_type=None)
        except Exception:  # noqa: BLE001
            continue
        _working_base = base
        return data or {"route": "cloud"}
    return {"route": "cloud"}


async def converse_local(hass: HomeAssistant, payload: dict) -> tuple[dict, int]:
    """Run a transcript through the add-on's ON-PREM brain (local model on the HA machine).

    POSTs to the add-on's /api/voice/converse-local (build plan §13.16/§13.17). The add-on
    runs the SAME brain core the cloud edge fn runs, but against a LAN model — nothing but the
    optional tool calls leaves the LAN. No account credential is needed (the add-on holds it
    internally and gates the route on the same household-sharing opt-in).

    Returns (turn_dict, status). Raises SharingDisabled on 403, AddonUnavailable if unreachable.
    """
    global _working_base
    session = async_get_clientsession(hass)
    bases = await _resolve_bases(session)

    last_err = "no candidates"
    for base in bases:
        url = f"{base}{_CONVERSE_LOCAL_PATH}"
        try:
            async with session.post(url, json=payload, timeout=_BRAIN_TIMEOUT) as resp:
                status = resp.status
                body = await resp.json(content_type=None) if status != 403 else None
        except Exception as err:  # noqa: BLE001
            last_err = f"{base}: {err}"
            continue

        # 403 = add-on reachable but household sharing is off — definitive, don't try other bases.
        if status == 403:
            _working_base = base
            raise SharingDisabled("household sharing disabled")

        _working_base = base
        return (body or {}), status

    raise AddonUnavailable(last_err)


def _parse_expiry(iso: str | None, now: float) -> float:
    if not iso:
        return now + 3600.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return now + 3600.0
