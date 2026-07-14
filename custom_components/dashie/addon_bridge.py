"""Bridge to the Dashie add-on for the account credential.

The account login/JWT lives in the **add-on** (the household account hub). The
integration fetches it to call cloud edge functions (the voice-conversation
"brain") on the account's behalf. Both run inside HA on the hassio network.

Reaching the add-on: we ask the **Supervisor** for the add-on's IP (bypasses
internal-DNS quirks), falling back to a few candidate hostnames. The resolved
base URL is cached once it works.

🔐 SECURITY (Lever 1): internal calls carry a shared bridge secret
(X-Dashie-Bridge-Secret) that the add-on provisions to its addon_config
(/config/addon_configs/dashie/bridge_secret — readable by this integration, not by
other add-ons). The add-on rejects unauthenticated callers once `bridge_auth_enforce`
is flipped on (observe-mode logs-but-allows until then). Build plan
20260702_BRIDGE_AUTH_HARDENING.md. Follow-up (Lever 2, not built): vend a scoped
token instead of the raw account JWT to shrink the blast radius of a leaked secret.
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
_AUTHORIZE_DEVICE_PATH = "/api/internal/authorize-device"
# On-prem brain (local model, runs IN the add-on — build plan §13.16/§13.17).
_CONVERSE_LOCAL_PATH = "/api/voice/converse-local"

# Shared bridge secret (Lever 1, build plan 20260702_BRIDGE_AUTH_HARDENING.md). The add-on
# provisions it to its addon_config, surfaced to HA Core at /config/addon_configs/dashie/. We
# read it once (cached) and present it on every /api/internal/* call so the add-on can reject
# unauthenticated callers on the hassio network. Absent (add-on not updated / not yet mapped) →
# no header → the add-on's observe mode allows it, so this is back/forward compatible.
_BRIDGE_HEADER = "X-Dashie-Bridge-Secret"
_BRIDGE_SECRET_REL = "addon_configs/dashie/bridge_secret"
_bridge_secret: str | None = None

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

_cache: dict = {"jwt": None, "exp": 0.0, "user_id": None}
_working_base: str | None = None

# Bound how long a cached account credential may outlive an ACCOUNT SWAP on the box.
# The JWT itself lives 72h, and the cache-hit path never re-asks the add-on — so before this
# cap, signing the add-on into a different account left the integration vending the PREVIOUS
# account's JWT for up to three days. On 2026-07-13 that had a kiosk minting voice tokens for
# a DELETED account: every turn failed the credit gate, and the user was told they were "out
# of voice credits" while their live account held $2. The add-on now PUSHES
# dashie.refresh_voice_config on sign-in/sign-out (→ clear_credential_cache below), which
# makes a swap take effect immediately; this cap is the belt-and-braces for a missed push
# (add-on older than 0.1.216, HA restarting, service call dropped). Cost of the cap: at most
# one localhost round-trip to the add-on every 5 minutes.
_CREDENTIAL_TTL = 300.0

# FB5: bound how often we re-probe household-sharing (revocation latency vs per-call cost).
_SHARING_TTL = 30.0
_sharing_cache: dict = {"off": False, "exp": 0.0}


class AddonUnavailable(Exception):
    """The Dashie add-on / account credential isn't reachable."""


class SharingDisabled(AddonUnavailable):
    """Add-on reachable + signed in, but household Dashie Cloud sharing is off.

    Subclasses AddonUnavailable so existing handlers still catch it, while
    callers that care can distinguish "off by choice" from "unreachable".
    """


async def _resolve_bases(session) -> list[str]:
    """Candidate add-on base URLs: the cached working one, else Supervisor
    discovery (dev-first) + the fallback hostnames."""
    if _working_base:
        return [_working_base]
    bases: list[str] = []
    bases.extend(await _discover_via_supervisor(session))
    bases.extend(_ADDON_CANDIDATES)
    return bases


def _is_dashie_addon(a: dict) -> bool:
    """A Dashie Console add-on of either channel. Repo-install slugs are
    `<repo>_dashie` (prod) / `<repo>_dashie_dev` (dev); names "Dashie Console"
    and "Dashie Console (Dev)"."""
    slug = a.get("slug") or ""
    name = a.get("name") or ""
    return (
        slug == "dashie"
        or slug.endswith("_dashie")
        or slug.endswith("dashie_dev")
        or name.startswith("Dashie Console")
    )


def _is_dev_addon(a: dict) -> bool:
    return (a.get("slug") or "").endswith("dashie_dev") or "(Dev)" in (a.get("name") or "")


async def _discover_via_supervisor(session) -> list[str]:
    """Resolve Dashie add-on base URLs (http://<ip>:8099) via the Supervisor API.

    Returns ALL installed Dashie add-ons, **dev channel first** — a dev box runs
    both channels and the developer signs into the dev add-on, so the integration
    must prefer it; a field box has only prod, so prod is picked. The caller uses
    the first base that actually answers (and caches it). Empty list when none
    found or no Supervisor token.
    """
    if not SUPERVISOR_TOKEN:
        _LOGGER.debug("no SUPERVISOR_TOKEN — skipping add-on discovery")
        return []
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    try:
        async with session.get(f"{SUPERVISOR_URL}/addons", headers=headers, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                _LOGGER.debug("supervisor /addons HTTP %s", resp.status)
                return []
            addons = ((await resp.json()).get("data") or {}).get("addons") or []
        matches = [a for a in addons if _is_dashie_addon(a)]
        if not matches:
            _LOGGER.debug("dashie add-on not found in supervisor list")
            return []
        # Dev first (developer intent), then prod.
        matches.sort(key=lambda a: 0 if _is_dev_addon(a) else 1)
        bases: list[str] = []
        for a in matches:
            slug = a.get("slug")
            try:
                async with session.get(f"{SUPERVISOR_URL}/addons/{slug}/info", headers=headers, timeout=_TIMEOUT) as resp:
                    if resp.status != 200:
                        continue
                    info = (await resp.json()).get("data") or {}
            except Exception:  # noqa: BLE001
                continue
            host = info.get("ip_address") or info.get("hostname")
            if host:
                bases.append(f"http://{host}:{ADDON_PORT}")
        return bases
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("supervisor discovery failed: %s", err)
        return []


async def _bridge_headers(hass: HomeAssistant) -> dict:
    """Auth header for /api/internal/* calls, or {} when the secret isn't provisioned yet
    (add-on not updated / addon_config not mapped) — the add-on's observe mode then allows it."""
    global _bridge_secret
    if _bridge_secret:
        return {_BRIDGE_HEADER: _bridge_secret}

    def _read() -> str | None:
        # HA Core reaches an add-on's addon_config either under its own config dir
        # (/config/addon_configs/<slug>/) or via a root mount (/addon_configs/<slug>/),
        # depending on the install — try both.
        for candidate in (hass.config.path(_BRIDGE_SECRET_REL), "/" + _BRIDGE_SECRET_REL):
            try:
                with open(candidate, encoding="utf-8") as fh:
                    val = (fh.read() or "").strip()
                    if val:
                        return val
            except (FileNotFoundError, OSError):
                continue
        return None

    secret = await hass.async_add_executor_job(_read)
    if secret:
        _bridge_secret = secret
        _LOGGER.info("Bridge secret loaded from addon_config")
        return {_BRIDGE_HEADER: secret}
    return {}


async def get_account_credential(hass: HomeAssistant) -> str:
    """Return the account JWT used to authenticate brain calls (cached until near expiry).

    FB5: also re-checks household-sharing (30s-TTL) even on a cache hit — otherwise a revoked
    sharing toggle wouldn't take effect until the cached JWT expired, so both the /session STT
    mint and the cloud converse path would keep spending the account's credits after a revoke.
    """
    global _working_base
    now = time.time()
    if await _sharing_is_off(hass):
        raise SharingDisabled("household sharing disabled")
    if _cache["jwt"] and now < _cache["exp"] - _REFRESH_SKEW:
        return _cache["jwt"]

    session = async_get_clientsession(hass)
    bases = await _resolve_bases(session)
    headers = await _bridge_headers(hass)

    last_err = "no candidates"
    for base in bases:
        url = f"{base}{_CREDENTIAL_PATH}"
        try:
            async with session.get(url, headers=headers, timeout=_TIMEOUT) as resp:
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
        user_id = (data or {}).get("user_id")
        if _cache["user_id"] and user_id and user_id != _cache["user_id"]:
            _LOGGER.info(
                "Account credential switched accounts (%s → %s)",
                _cache["user_id"], user_id,
            )
        _cache["jwt"] = jwt
        _cache["user_id"] = user_id
        # Cap the cache lifetime — see _CREDENTIAL_TTL. The JWT's own expiry still wins when
        # it is SOONER (a nearly-expired token must not be handed out).
        _cache["exp"] = min(
            _parse_expiry(data.get("jwt_expires_at"), now),
            now + _CREDENTIAL_TTL + _REFRESH_SKEW,
        )
        _LOGGER.info("Account credential fetched from add-on at %s", base)
        return jwt

    _working_base = None
    raise AddonUnavailable(last_err)


def clear_credential_cache() -> None:
    """Drop the cached account credential so the next call re-asks the add-on.

    Called by the `dashie.refresh_voice_config` service, which the add-on fires on sign-in and
    sign-out. Without this, an account swap on the box left us vending the OLD account's JWT
    until it expired (72h) — see _CREDENTIAL_TTL for what that cost.
    """
    if _cache["jwt"]:
        _LOGGER.info("Account credential cache cleared (account change or explicit refresh)")
    _cache["jwt"] = None
    _cache["exp"] = 0.0
    _cache["user_id"] = None
    _sharing_cache["exp"] = 0.0   # re-probe sharing too — it's account-scoped.


async def get_sharing_status(hass: HomeAssistant) -> dict:
    """Probe the add-on's sharing-status endpoint (capability check, no credential).

    Returns the add-on's `{available, signed_in, household_sharing, reason}` dict,
    or a synthesized `{available: False, reason: "addon_unreachable"}` when the
    add-on can't be reached. Never raises.
    """
    global _working_base
    session = async_get_clientsession(hass)
    bases = await _resolve_bases(session)
    headers = await _bridge_headers(hass)
    for base in bases:
        try:
            async with session.get(f"{base}{_SHARING_STATUS_PATH}", headers=headers, timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json(content_type=None)
        except Exception:  # noqa: BLE001
            continue
        _working_base = base
        return data or {"available": False, "reason": "bad_response"}
    return {"available": False, "reason": "addon_unreachable"}


async def _sharing_is_off(hass: HomeAssistant) -> bool:
    """True only when the add-on POSITIVELY reports household-sharing OFF (30s-TTL cached).

    FB5: get_account_credential serves a cached JWT, so without this a revoked sharing toggle
    wouldn't take effect until the JWT expired. Cached for _SHARING_TTL so we don't probe on
    every credential fetch; an unreachable/ambiguous status is treated as "not off" (fall through
    to existing behavior — no new failure modes, no per-turn latency spike).
    """
    now = time.time()
    if now < _sharing_cache["exp"]:
        return _sharing_cache["off"]
    status = await get_sharing_status(hass)  # never raises
    off = status.get("household_sharing") is False
    _sharing_cache["off"] = off
    _sharing_cache["exp"] = now + _SHARING_TTL
    return off


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
    headers = await _bridge_headers(hass)
    for base in bases:
        try:
            async with session.get(f"{base}{_VOICE_CONFIG_PATH}", headers=headers, timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json(content_type=None)
        except Exception:  # noqa: BLE001
            continue
        _working_base = base
        return data or {"route": "cloud"}
    return {"route": "cloud"}


async def authorize_device(hass: HomeAssistant, user_code: str) -> tuple[dict, int]:
    """Ask the add-on to authorize a pending kiosk device code into the household account.

    Kiosk Real Login, Phase 1. A LAN tablet has created a device code and wants a REAL account
    session instead of the anonymous-kiosk mirror. The add-on holds the account JWT, so it calls
    jwt-auth's `authorize_device_code_account` on the tablet's behalf; the TABLET then polls
    jwt-auth directly for its own per-device JWT.

    No credential passes through here — we return only success/failure. The add-on gates on
    household sharing, and jwt-auth re-checks it server-side (the authoritative gate) and
    restricts the operation to `device_type='ha_kiosk'`.

    Returns (body, status). Never raises — the caller surfaces the status to the tablet.
    """
    global _working_base
    session = async_get_clientsession(hass)
    bases = await _resolve_bases(session)
    headers = await _bridge_headers(hass)

    last_err = "no candidates"
    for base in bases:
        try:
            async with session.post(
                f"{base}{_AUTHORIZE_DEVICE_PATH}",
                json={"user_code": user_code},
                headers=headers,
                timeout=_TIMEOUT,
            ) as resp:
                status = resp.status
                body = await resp.json(content_type=None)
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            continue
        _working_base = base
        return (body or {}, status)

    return ({"error": "addon_unavailable", "message": f"Dashie add-on unreachable: {last_err}"}, 503)


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
