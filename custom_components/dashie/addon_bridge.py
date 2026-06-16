"""Bridge to the Dashie add-on for the account credential.

The account login/JWT lives in the **add-on** (the household account hub). The
integration fetches it to call cloud edge functions (the voice-conversation
"brain") on the account's behalf. Both run inside HA on the hassio network.

⚠️ v1 SECURITY = network-trust. The add-on's `/api/internal/account-credential`
is ingress-only (not externally exposed) but is not yet authenticated, so any
component on the hassio network could call it. Acceptable for single-household
dev; HARDEN before wider use (shared secret config_flow option ↔ add-on option,
or a short-lived scoped token). Tracked in tech-debt.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from aiohttp import ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

# Candidate add-on addresses on the hassio Docker network (slug 'dashie', internal
# port 8099). The exact hostname depends on how the add-on was installed (local vs
# repository), so we probe a few and cache the one that works.
# TODO(config): make this a config_flow option / discover via the Supervisor API.
_ADDON_CANDIDATES = (
    "http://local-dashie:8099",
    "http://local_dashie:8099",
    "http://addon_local_dashie:8099",
    "http://dashie:8099",
)
_CREDENTIAL_PATH = "/api/internal/account-credential"

_REFRESH_SKEW = 120.0            # refresh this many seconds before expiry
_TIMEOUT = ClientTimeout(total=5)

_cache: dict = {"jwt": None, "exp": 0.0}
_working_base: str | None = None


class AddonUnavailable(Exception):
    """The Dashie add-on / account credential isn't reachable."""


async def get_account_credential(hass: HomeAssistant) -> str:
    """Return the account JWT used to authenticate brain calls (cached until near expiry)."""
    global _working_base
    now = time.time()
    if _cache["jwt"] and now < _cache["exp"] - _REFRESH_SKEW:
        return _cache["jwt"]

    session = async_get_clientsession(hass)
    bases = [_working_base] if _working_base else list(_ADDON_CANDIDATES)
    last_err = "no candidates"

    for base in bases:
        if not base:
            continue
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
        _LOGGER.debug("Account credential fetched from add-on at %s", base)
        return jwt

    _working_base = None  # re-probe next time
    raise AddonUnavailable(last_err)


def _parse_expiry(iso: str | None, now: float) -> float:
    if not iso:
        return now + 3600.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return now + 3600.0
