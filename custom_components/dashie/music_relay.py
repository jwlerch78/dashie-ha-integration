"""Music Assistant API relay for Dashie.

Proxies MA REST API commands through HA so that remote/proxy-connected
tablets can control music without direct LAN access to the MA server.

Uses the centrally stored MA JWT token from MusicTokenStore.

HTTP endpoints:
  POST /api/dashie/music/relay       — relay an MA API command (REST)
  POST /api/dashie/music/ws-command  — relay a single command via MA WebSocket
  GET  /api/dashie/music/imageproxy  — relay an MA image proxy request
"""
from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .music_token_store import MusicTokenStore

_LOGGER = logging.getLogger(__name__)

# Timeout for outbound requests to the local MA server
MA_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class DashieMusicRelayView(HomeAssistantView):
    """Relay MA API commands through HA.

    Accepts the same JSON body format as the MA REST API:
      {"command": "players/all", "player_id": "...", ...}

    Reads the MA token + URL from the central MusicTokenStore,
    then forwards the request to the local MA server and returns
    the response verbatim.
    """

    url = "/api/dashie/music/relay"
    name = "api:dashie:music:relay"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: MusicTokenStore | None = hass.data.get("dashie", {}).get(
            "music_token_store"
        )
        if store is None:
            return web.json_response(
                {"error": "Music token store not initialized"}, status=500
            )

        token_data = store.get_token()
        token = token_data.get("token", "")
        ma_url = token_data.get("ma_url", "")
        if not token or not ma_url:
            return web.json_response(
                {"error": "No MA token configured — log in on any device first"},
                status=400,
            )

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        command = body.get("command", "")
        if not command:
            return web.json_response(
                {"error": "Missing 'command' field"}, status=400
            )

        # Forward the entire body to MA (it already has message_id, command, args)
        ma_api_url = f"{ma_url.rstrip('/')}/api"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        try:
            async with aiohttp.ClientSession(timeout=MA_REQUEST_TIMEOUT) as session:
                async with session.post(
                    ma_api_url, json=body, headers=headers
                ) as resp:
                    response_text = await resp.text()
                    return web.Response(
                        text=response_text,
                        status=resp.status,
                        content_type="application/json",
                    )
        except aiohttp.ClientError as err:
            _LOGGER.error("MA relay failed for %s: %s", command, err)
            return web.json_response(
                {"error": f"MA server unreachable: {err}"}, status=502
            )
        except Exception as err:
            _LOGGER.exception("MA relay unexpected error for %s", command)
            return web.json_response(
                {"error": f"Relay error: {err}"}, status=500
            )


class DashieMusicImageProxyView(HomeAssistantView):
    """Relay MA image proxy requests through HA.

    Query params (passed through to MA):
      size   — image size (e.g. 256)
      fmt    — image format (e.g. jpeg)
      path   — double-URL-encoded image path

    Returns the proxied image bytes with the correct content type.
    """

    url = "/api/dashie/music/imageproxy"
    name = "api:dashie:music:imageproxy"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: MusicTokenStore | None = hass.data.get("dashie", {}).get(
            "music_token_store"
        )
        if store is None:
            return web.Response(status=500, text="Music token store not initialized")

        token_data = store.get_token()
        token = token_data.get("token", "")
        ma_url = token_data.get("ma_url", "")
        if not token or not ma_url:
            return web.Response(status=400, text="No MA token configured")

        # Forward query params to MA imageproxy
        query_string = request.query_string
        if not query_string:
            return web.Response(status=400, text="Missing query parameters")

        proxy_url = f"{ma_url.rstrip('/')}/imageproxy?{query_string}"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            async with aiohttp.ClientSession(timeout=MA_REQUEST_TIMEOUT) as session:
                async with session.get(proxy_url, headers=headers) as resp:
                    if resp.status != 200:
                        return web.Response(status=resp.status)
                    image_data = await resp.read()
                    content_type = resp.headers.get(
                        "Content-Type", "image/jpeg"
                    )
                    return web.Response(
                        body=image_data,
                        content_type=content_type,
                    )
        except aiohttp.ClientError as err:
            _LOGGER.error("MA imageproxy relay failed: %s", err)
            return web.Response(status=502, text=f"MA server unreachable: {err}")
        except Exception as err:
            _LOGGER.exception("MA imageproxy relay unexpected error")
            return web.Response(status=500, text=f"Relay error: {err}")


class DashieMusicWsCommandView(HomeAssistantView):
    """Relay a single command via MA WebSocket.

    Some MA commands (e.g. config/providers/save) are only available
    on the WebSocket API, not the REST API. This endpoint opens a
    temporary WebSocket connection, sends the command, reads the
    response, and returns it.

    Accepts the same JSON body format as the REST relay:
      {"command": "config/providers/save", "provider_domain": "snapcast", ...}
    """

    url = "/api/dashie/music/ws-command"
    name = "api:dashie:music:ws_command"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        store: MusicTokenStore | None = hass.data.get("dashie", {}).get(
            "music_token_store"
        )
        if store is None:
            return web.json_response(
                {"error": "Music token store not initialized"}, status=500
            )

        token_data = store.get_token()
        token = token_data.get("token", "")
        ma_url = token_data.get("ma_url", "")
        if not token or not ma_url:
            return web.json_response(
                {"error": "No MA token configured"}, status=400
            )

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        command = body.get("command", "")
        if not command:
            return web.json_response(
                {"error": "Missing 'command' field"}, status=400
            )

        # Build WebSocket URL from MA base URL (http→ws, https→wss)
        ws_url = ma_url.rstrip("/").replace("http://", "ws://").replace(
            "https://", "wss://"
        ) + "/ws"

        try:
            async with aiohttp.ClientSession(
                timeout=MA_REQUEST_TIMEOUT
            ) as session:
                async with session.ws_connect(ws_url) as ws:
                    # 1. Read server_info message (sent immediately on connect)
                    server_info = await asyncio.wait_for(
                        ws.receive_json(), timeout=5
                    )
                    _LOGGER.debug("MA WS server_info: %s", server_info)

                    # 2. Authenticate — command is "auth", token in "args"
                    await ws.send_json({
                        "message_id": "auth-0",
                        "command": "auth",
                        "args": {"token": token},
                    })
                    auth_resp = await asyncio.wait_for(
                        ws.receive_json(), timeout=5
                    )
                    if auth_resp.get("error_code"):
                        return web.json_response(
                            {"error": f"MA auth failed: {auth_resp}"},
                            status=401,
                        )

                    # 3. Send the actual command
                    await ws.send_json(body)

                    # 4. Read response — skip notifications until we get
                    # a response matching our message_id
                    msg_id = body.get("message_id")
                    deadline = asyncio.get_event_loop().time() + 8
                    while asyncio.get_event_loop().time() < deadline:
                        resp = await asyncio.wait_for(
                            ws.receive_json(), timeout=8
                        )
                        # Match by message_id if provided, else take first
                        if msg_id is None or resp.get("message_id") == msg_id:
                            return web.json_response(resp)
                    return web.json_response(
                        {"error": "Timeout waiting for response"}, status=504
                    )

        except asyncio.TimeoutError:
            _LOGGER.error("MA WebSocket timeout for %s", command)
            return web.json_response(
                {"error": "MA WebSocket timeout"}, status=504
            )
        except aiohttp.ClientError as err:
            _LOGGER.error("MA WebSocket failed for %s: %s", command, err)
            return web.json_response(
                {"error": f"MA server unreachable: {err}"}, status=502
            )
        except Exception as err:
            _LOGGER.exception("MA WebSocket unexpected error for %s", command)
            return web.json_response(
                {"error": f"WS relay error: {err}"}, status=500
            )


def register_music_relay_views(hass: HomeAssistant) -> None:
    """Register music relay HTTP views."""
    hass.http.register_view(DashieMusicRelayView())
    hass.http.register_view(DashieMusicImageProxyView())
    hass.http.register_view(DashieMusicWsCommandView())
    _LOGGER.info("Registered Dashie music relay views")
