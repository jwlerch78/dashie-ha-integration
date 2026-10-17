"""Routing the integration's HTTP at a FakeSession.

Split out of `fake_http.py`, which was over the 250-line utility ceiling: that file
answers "what does a fake session do", this one answers "how do we make the
integration use it". The second question is where the version-specific knowledge
lives, so it is the one that will keep changing.
"""
from __future__ import annotations

from .fake_http import FakeSession


def patch_sessions(fake: FakeSession):
    """Route EVERY way the integration gets an HTTP session to `fake`.

    For when the coordinator is built *inside* the code under test (e.g. by
    `async_setup_entry`), so there is no instance to assign `_session` on. Where you
    do have the instance, prefer `coord._session = fake`: fewer moving parts.

    Assert on `fake.requests` afterwards. A test whose injection silently misses runs
    against a real (failing) session and can still pass on assertions that do not
    depend on the response, which is how these tests passed on HA 2026.9.3 with
    aioresponses dead.
    """
    from contextlib import ExitStack, contextmanager
    from unittest.mock import AsyncMock, patch

    from custom_components.dashie.coordinator import DashieCoordinator

    @contextmanager
    def _both():
        with ExitStack() as stack:
            stack.enter_context(patch.object(
                DashieCoordinator, "_get_session", AsyncMock(return_value=fake)))
            # The integration takes a session TWO ways, and covering only the first
            # leaves real sockets open. The coordinator builds its own
            # (coordinator.py:84); addon_bridge/camera/config_flow/image ask HA for the
            # shared one. A successful first poll reaches `_async_register_services`,
            # whose 1s timer pushes to devices down the SECOND path, so a test that
            # patches only the coordinator trips HA 2026.9's socket guard at teardown.
            # 🔴 PATCH WHERE THE NAME IS LOOKED UP, NOT WHERE IT IS DEFINED. Each of
            # these modules does `from ...aiohttp_client import async_get_clientsession`
            # at import time, so patching the helpers module leaves their own binding
            # untouched and a REAL session is still built. Measured: with only the
            # helpers-module patch, setup still reached aiohttp's connector
            # (aiohappyeyeballs._connect_sock) from a detached task and tripped HA
            # 2026.9's socket guard at teardown.
            # create=False is deliberate: if one of these stops importing the name, the
            # patch must FAIL rather than quietly cover nothing.
            # music_token_store does the same import (music_token_store.py:40), so it
            # belongs in "every way the integration gets a session".
            # ⚠️ BUT IT IS COMPLETENESS, NOT A FIX, and the distinction is the point:
            # as of 2026-10-17 its only production caller is the GET view
            # (music_token_store.py:133), which no patch_sessions test reaches, so this
            # entry currently has NO READER and nothing here is load-bearing. It is
            # listed so that a future test which does reach that view fails loudly on a
            # moved import instead of quietly opening a real socket.
            # 🔴 Do not cite it as the cause of the aioresponses damage recorded in
            # test_ma_url_heal.py — that was the dead mock, not a missing entry here.
            for mod in ("addon_bridge", "camera", "image", "config_flow", "voice_view",
                        "music_token_store"):
                stack.enter_context(patch(
                    f"custom_components.dashie.{mod}.async_get_clientsession",
                    return_value=fake, create=False))
            # ⚠️ A THIRD consumer kind, and this one is a BEHAVIOR stub, not a session
            # injection - do not mistake it for coverage. `go2rtc_manager` builds its
            # session inline (go2rtc_manager.py:182), so there is no seam to inject at
            # without changing production code. `async_setup_entry` awaits
            # `manager.detect()` at __init__.py:275, which probes FOUR real endpoints
            # (localhost:1984, 127.0.0.1:1984, two Frigate hostnames). Returning False
            # states "no go2rtc here", which is what those probes would conclude in a
            # test environment anyway.
            # This only became reachable once the HTTP mock worked: a failed first poll
            # used to short-circuit setup before line 275, so the real connections were
            # hidden by the broken mock rather than absent.
            stack.enter_context(patch(
                "custom_components.dashie.go2rtc_manager.Go2RtcManager.detect",
                AsyncMock(return_value=False)))
            yield fake

    return _both()
