"""Lightweight RTSP relay for Dashie.

Proxies RTSP connections from ExoPlayer tablets to upstream cameras,
handling authentication so tablets don't need credentials.
Replaces go2rtc for this specific use case.

Each registered stream maps a name to an upstream RTSP URL with credentials.
When a client connects and requests a stream, the relay:
1. Connects upstream to the real camera
2. Handles RTSP Digest/Basic auth with the camera
3. Forwards RTSP responses (rewriting URLs) and RTP data to the client

Only supports TCP interleaved mode (RTP over RTSP connection), which is
what ExoPlayer uses with setForceUseRtpTcp(true).

Usage:
    relay = RtspRelayServer(port=8555)
    relay.register_stream("camera.pool_sd", "rtsp://user:pass@192.168.86.50:554/stream2")
    await relay.start()
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from urllib.parse import urlparse, unquote

_LOGGER = logging.getLogger(__name__)

_RELAY_PORT = 8555


def _compute_digest_response(
    username: str, password: str, realm: str, nonce: str,
    method: str, uri: str,
) -> str:
    """Compute HTTP Digest auth response (MD5, no qop)."""
    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    return hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()


def _build_auth_header(
    username: str, password: str, method: str, uri: str,
    www_authenticate: str,
) -> str:
    """Build an Authorization header from a WWW-Authenticate challenge."""
    www_lower = www_authenticate.lower()
    if www_lower.startswith("digest"):
        # Parse realm and nonce from: Digest realm="...", nonce="..."
        params: dict[str, str] = {}
        for part in www_authenticate[7:].split(","):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                params[k.strip().lower()] = v.strip().strip('"')
        realm = params.get("realm", "")
        nonce = params.get("nonce", "")
        response = _compute_digest_response(username, password, realm, nonce, method, uri)
        return (
            f'Digest username="{username}", realm="{realm}", '
            f'nonce="{nonce}", uri="{uri}", response="{response}"'
        )
    elif www_lower.startswith("basic"):
        import base64
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        return f"Basic {encoded}"
    return ""


class RtspRelayServer:
    """Async RTSP relay that proxies client connections to upstream cameras."""

    def __init__(self, port: int = _RELAY_PORT) -> None:
        self._port = port
        self._streams: dict[str, str] = {}  # name → upstream rtsp://user:pass@host/path
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        return self._port

    def register_stream(self, name: str, upstream_url: str) -> None:
        """Register a stream name → upstream RTSP URL mapping."""
        self._streams[name] = upstream_url
        _LOGGER.info("RTSP relay: registered '%s'", name)

    def unregister_stream(self, name: str) -> None:
        """Remove a stream mapping."""
        self._streams.pop(name, None)

    def has_stream(self, name: str) -> bool:
        return name in self._streams

    def get_stream_names(self) -> list[str]:
        return list(self._streams.keys())

    async def start(self) -> None:
        """Start listening for RTSP client connections."""
        import socket
        self._server = await asyncio.start_server(
            self._handle_client, "0.0.0.0", self._port,
            reuse_address=True,
        )
        _LOGGER.info("RTSP relay listening on port %d", self._port)

    async def stop(self) -> None:
        """Stop the relay server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            _LOGGER.info("RTSP relay stopped")

    async def _handle_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single RTSP client connection."""
        peer = client_writer.get_extra_info("peername")
        _LOGGER.info("RTSP relay: client connected from %s", peer)

        upstream_reader: asyncio.StreamReader | None = None
        upstream_writer: asyncio.StreamWriter | None = None
        stream_name: str | None = None
        up_parsed = None  # parsed upstream URL
        up_base_url: str = ""  # rtsp://host:port (no creds)

        try:
            while True:
                # Read an RTSP request or interleaved RTP frame from client
                first_byte = await client_reader.readexactly(1)
                if first_byte == b"$":
                    # Interleaved RTP/RTCP: $<channel:1><length:2><data>
                    header = await client_reader.readexactly(3)
                    length = int.from_bytes(header[1:3], "big")
                    data = await client_reader.readexactly(length)
                    if upstream_writer:
                        upstream_writer.write(b"$" + header + data)
                        await upstream_writer.drain()
                    continue

                # RTSP text request — read until \r\n\r\n
                request_data = first_byte + await _read_until_crlfcrlf(client_reader)
                request_text = request_data.decode("utf-8", errors="replace")

                lines = request_text.split("\r\n")
                if not lines:
                    break
                parts = lines[0].split(" ", 2)
                if len(parts) < 3:
                    break
                method, request_uri, version = parts

                # Extract stream name from URI path
                parsed_uri = urlparse(request_uri)
                path_parts = parsed_uri.path.strip("/").split("/")
                requested_name = path_parts[0] if path_parts else ""

                # First request: resolve stream and connect upstream
                if not stream_name:
                    stream_name = requested_name
                    upstream_url = self._streams.get(stream_name)
                    if not upstream_url:
                        _LOGGER.warning("RTSP relay: unknown stream '%s'", stream_name)
                        client_writer.write(
                            b"RTSP/1.0 404 Not Found\r\nCSeq: 1\r\n\r\n"
                        )
                        await client_writer.drain()
                        break

                    up_parsed = urlparse(upstream_url)
                    up_host = up_parsed.hostname or ""
                    up_port = up_parsed.port or 554
                    up_base_url = f"rtsp://{up_host}:{up_port}"

                    _LOGGER.info(
                        "RTSP relay: connecting upstream '%s' → %s:%d",
                        stream_name, up_host, up_port,
                    )
                    try:
                        upstream_reader, upstream_writer = await asyncio.wait_for(
                            asyncio.open_connection(up_host, up_port), timeout=5.0
                        )
                    except Exception as e:
                        _LOGGER.error("RTSP relay: upstream connect failed: %s", e)
                        client_writer.write(
                            b"RTSP/1.0 503 Service Unavailable\r\nCSeq: 1\r\n\r\n"
                        )
                        await client_writer.drain()
                        break

                # Build the upstream URI (no credentials in URL)
                extra_path = "/".join(path_parts[1:]) if len(path_parts) > 1 else ""
                upstream_path = up_parsed.path
                if extra_path:
                    upstream_path = upstream_path.rstrip("/") + "/" + extra_path
                upstream_uri = f"{up_base_url}{upstream_path}"

                # Build upstream request (same method, rewritten URI, same headers)
                up_request_line = f"{method} {upstream_uri} {version}"
                # Filter out any Host header from client, keep the rest
                up_headers = []
                for line in lines[1:]:
                    if line and not line.lower().startswith("host:"):
                        up_headers.append(line)
                up_request = up_request_line + "\r\n" + "\r\n".join(up_headers)

                _LOGGER.debug("RTSP relay: → upstream: %s %s", method, upstream_uri)

                # Send to upstream
                upstream_writer.write(up_request.encode())
                await upstream_writer.drain()

                # Read upstream response
                response_data = await _read_rtsp_response(upstream_reader)
                if response_data is None:
                    _LOGGER.warning("RTSP relay: upstream closed during %s", method)
                    break

                response_text = response_data.decode("utf-8", errors="replace")
                resp_lines = response_text.split("\r\n")
                status_line = resp_lines[0] if resp_lines else ""

                _LOGGER.debug("RTSP relay: ← upstream: %s", status_line)

                # Handle 401 — authenticate with upstream using stored credentials
                if " 401 " in status_line:
                    www_auth = ""
                    for rl in resp_lines:
                        if rl.lower().startswith("www-authenticate:"):
                            www_auth = rl.split(":", 1)[1].strip()
                            # Prefer Digest over Basic
                            if "digest" in www_auth.lower():
                                break

                    if www_auth and up_parsed and up_parsed.username:
                        username = unquote(up_parsed.username)
                        password = unquote(up_parsed.password or "")
                        auth_value = _build_auth_header(
                            username, password, method, upstream_uri, www_auth
                        )
                        if auth_value:
                            _LOGGER.debug("RTSP relay: retrying %s with auth", method)
                            # Resend the request with Authorization header
                            auth_headers = up_headers.copy()
                            # Insert Authorization before the empty trailing line
                            if auth_headers and auth_headers[-1] == "":
                                auth_headers.insert(-1, f"Authorization: {auth_value}")
                            else:
                                auth_headers.append(f"Authorization: {auth_value}")
                                auth_headers.append("")
                            auth_request = up_request_line + "\r\n" + "\r\n".join(auth_headers)
                            upstream_writer.write(auth_request.encode())
                            await upstream_writer.drain()

                            # Read the real response
                            response_data = await _read_rtsp_response(upstream_reader)
                            if response_data is None:
                                break
                            response_text = response_data.decode("utf-8", errors="replace")
                            resp_lines = response_text.split("\r\n")
                            status_line = resp_lines[0] if resp_lines else ""
                            _LOGGER.debug("RTSP relay: ← upstream (after auth): %s", status_line)

                # Rewrite upstream URLs in response to relay URLs
                relay_base = f"rtsp://{parsed_uri.hostname}:{self._port}/{stream_name}"
                response_text = response_text.replace(up_base_url, relay_base)

                # Forward response to client
                client_writer.write(response_text.encode())
                await client_writer.drain()

                # After PLAY, switch to bidirectional binary relay
                if method == "PLAY" and " 200 " in status_line:
                    _LOGGER.info("RTSP relay: PLAY OK for '%s', starting data relay", stream_name)
                    await self._relay_bidirectional(
                        client_reader, client_writer,
                        upstream_reader, upstream_writer,
                        stream_name,
                    )
                    break

                if method == "TEARDOWN":
                    break

        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            _LOGGER.debug("RTSP relay: client %s disconnected", peer)
        except Exception:
            _LOGGER.exception("RTSP relay: error handling client %s", peer)
        finally:
            client_writer.close()
            if upstream_writer:
                upstream_writer.close()
            _LOGGER.info("RTSP relay: session ended for '%s' from %s", stream_name, peer)

    async def _relay_bidirectional(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
        stream_name: str,
    ) -> None:
        """Bidirectional byte relay after PLAY — forward RTP/RTCP frames."""

        async def _upstream_to_client() -> None:
            try:
                while True:
                    data = await upstream_reader.read(65536)
                    if not data:
                        break
                    client_writer.write(data)
                    await client_writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass

        async def _client_to_upstream() -> None:
            try:
                while True:
                    data = await client_reader.read(65536)
                    if not data:
                        break
                    upstream_writer.write(data)
                    await upstream_writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass

        tasks = [
            asyncio.create_task(_upstream_to_client()),
            asyncio.create_task(_client_to_upstream()),
        ]
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()


async def _read_until_crlfcrlf(reader: asyncio.StreamReader) -> bytes:
    """Read from the stream until \\r\\n\\r\\n (end of RTSP headers)."""
    buf = bytearray()
    while True:
        byte = await reader.readexactly(1)
        buf.extend(byte)
        if buf[-4:] == b"\r\n\r\n":
            return bytes(buf)


async def _read_rtsp_response(reader: asyncio.StreamReader) -> bytes | None:
    """Read a complete RTSP response (headers + optional body via Content-Length)."""
    header_buf = bytearray()
    while True:
        byte = await reader.readexactly(1)
        header_buf.extend(byte)
        if header_buf[-4:] == b"\r\n\r\n":
            break

    headers_text = header_buf.decode("utf-8", errors="replace")
    content_length = 0
    for line in headers_text.split("\r\n"):
        if line.lower().startswith("content-length:"):
            try:
                content_length = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
            break

    body = b""
    if content_length > 0:
        body = await reader.readexactly(content_length)

    return bytes(header_buf) + body
