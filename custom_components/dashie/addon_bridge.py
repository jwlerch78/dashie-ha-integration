"""Bridge to the Dashie add-on for the account credential.

The account login/JWT lives in the **add-on** (the household account hub). The
integration fetches it to call cloud edge functions (the voice-conversation
"brain") on the account's behalf. Both run inside HA.

⚠️ AWAITING ADD-ON COORDINATION. To finish this, three things must be settled
with the add-on (`dashie-ha-app`):
  1. **Reachability** — how the integration reaches the add-on (hostname:port on
     the hassio Docker network, e.g. ``http://local_dashie:8099``, or a
     configured base URL).
  2. **Auth** — how the integration authenticates to the add-on's credential
     endpoint (shared secret / supervisor token) so it isn't an open endpoint.
  3. **Endpoint** — the add-on route that returns the account JWT (or a
     short-lived token). The add-on already has ``/api/auth/jwt`` — confirm it's
     reachable + appropriately gated for internal callers.

Until those land, ``get_account_credential()`` raises ``AddonUnavailable`` and
the voice gateway returns 503 (so the tablet degrades gracefully).
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class AddonUnavailable(Exception):
    """The Dashie add-on / account credential isn't reachable."""


async def get_account_credential(hass: HomeAssistant) -> str:
    """Return the account JWT used to authenticate brain calls.

    TODO(add-on): implement — fetch from the add-on's credential endpoint with a
    trusted-caller auth and cache until near expiry (see module docstring).
    """
    raise AddonUnavailable("credential bridge not yet wired (awaiting add-on endpoint)")
