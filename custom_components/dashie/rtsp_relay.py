"""Lightweight RTSP relay for Dashie.

Proxies RTSP connections from ExoPlayer tablets to upstream cameras,
stripping credentials so tablets don't need to handle authentication.
Replaces go2rtc for this specific use case.

Each registered stream maps a name to an upstream RTSP URL with credentials.
When a client connects and requests a stream, the relay:
1. Connects upstream to the real camera (with credentials)
2. Rewrites RTSP request URLs to point at the real camera
3. Forwards all RTSP/RTP data bidirectionally (no transcoding)

Only supports TCP interleaved mode (RTP over RTSP connection), which is
what ExoPlayer uses with setForceUseRtpTcp(true).

Usage:
    relay = RtspRelayServer(port=8555)
    relay.register_stream("camera.pool_sd", "rtsp://user:pass@192.168.86.50:554/stream2")
    await relay.start()
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse, urlunparse

_LOGGER = logging.getLogger(__name__)

_RELAY_PORT = 8555


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
        self._server = await asyncio.start_server(
            self._handle_client, "0.0.0.0", self._port
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
        upstream_base_url: str | None = None  # e.g. rtsp://host:554

        try:
            # Phase 1: Read RTSP requests from client, relay to upstream.
            # We need to parse the first request to determine which stream
            # the client wants, then connect upstream.
            while True:
                # Read an RTSP request or interleaved RTP frame
                first_byte = await client_reader.readexactly(1)
                if first_byte == b"$":
                    # Interleaved RTP frame: $<channel:1><length:2><data:length>
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

                # Parse the request line: METHOD rtsp://host:port/path RTSP/1.0
                lines = request_text.split("\r\n")
                if not lines:
                    break
                parts = lines[0].split(" ", 2)
                if len(parts) < 3:
                    break
                method, request_uri, version = parts

                # Extract stream name from the URI path
                parsed = urlparse(request_uri)
                # Path is like /camera.pool_camera_sd_stream or /stream_name/trackID=0
                path_parts = parsed.path.strip("/").split("/")
                requested_name = path_parts[0] if path_parts else ""

                if not stream_name:
                    stream_name = requested_name
                    upstream_url = self._streams.get(stream_name)
                    if not upstream_url:
                        _LOGGER.warning(
                            "RTSP relay: unknown stream '%s' from %s",
                            stream_name, peer,
                        )
                        # Send 404 and close
                        client_writer.write(
                            f"RTSP/1.0 404 Not Found\r\nCSeq: 1\r\n\r\n".encode()
                        )
                        await client_writer.drain()
                        break

                    # Connect upstream
                    up_parsed = urlparse(upstream_url)
                    up_host = up_parsed.hostname or ""
                    up_port = up_parsed.port or 554
                    upstream_base_url = f"rtsp://{up_host}:{up_port}"

                    _LOGGER.info(
                        "RTSP relay: connecting upstream for '%s' → %s:%d",
                        stream_name, up_host, up_port,
                    )
                    try:
                        upstream_reader, upstream_writer = await asyncio.wait_for(
                            asyncio.open_connection(up_host, up_port), timeout=5.0
                        )
                    except Exception as e:
                        _LOGGER.error(
                            "RTSP relay: upstream connect failed for '%s': %s",
                            stream_name, e,
                        )
                        client_writer.write(
                            f"RTSP/1.0 503 Service Unavailable\r\nCSeq: 1\r\n\r\n".encode()
                        )
                        await client_writer.drain()
                        break

                # Rewrite the request URI to point at the upstream camera
                # Client sends: DESCRIBE rtsp://ha:8555/camera.pool/trackID=0 RTSP/1.0
                # We send:      DESCRIBE rtsp://user:pass@camera:554/stream2/trackID=0 RTSP/1.0
                up_parsed = urlparse(self._streams[stream_name])
                # Preserve any sub-path from the client (e.g. /trackID=0)
                extra_path = "/".join(path_parts[1:]) if len(path_parts) > 1 else ""
                up_path = up_parsed.path
                if extra_path:
                    up_path = up_path.rstrip("/") + "/" + extra_path

                rewritten_uri = urlunparse((
                    up_parsed.scheme,
                    up_parsed.netloc,  # includes user:pass@host:port
                    up_path,
                    up_parsed.params,
                    up_parsed.query,
                    up_parsed.fragment,
                ))
                rewritten_line = f"{method} {rewritten_uri} {version}"
                rewritten_request = rewritten_line + "\r\n" + "\r\n".join(lines[1:])

                # Forward to upstream
                upstream_writer.write(rewritten_request.encode())
                await upstream_writer.drain()

                # Read response from upstream and forward to client
                response = await _read_rtsp_response(upstream_reader)
                if response is None:
                    break

                # Rewrite any upstream URLs in the response back to our relay URL
                response_text = response.decode("utf-8", errors="replace")
                if upstream_base_url:
                    relay_base = f"rtsp://{parsed.hostname}:{self._port}"
                    up_base_with_creds = f"{up_parsed.scheme}://{up_parsed.netloc}"
                    response_text = response_text.replace(
                        up_base_with_creds, f"{relay_base}/{stream_name}"
                    )
                    response_text = response_text.replace(
                        upstream_base_url, f"{relay_base}/{stream_name}"
                    )

                client_writer.write(response_text.encode())
                await client_writer.drain()

                # After PLAY, switch to bidirectional binary relay
                if method == "PLAY":
                    _LOGGER.info(
                        "RTSP relay: PLAY for '%s', starting bidirectional relay",
                        stream_name,
                    )
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
            _LOGGER.info(
                "RTSP relay: session ended for '%s' from %s", stream_name, peer
            )

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
            """Forward data from upstream camera to client tablet."""
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
            """Forward data from client tablet to upstream camera (RTCP, TEARDOWN)."""
            try:
                while True:
                    data = await client_reader.read(65536)
                    if not data:
                        break
                    upstream_writer.write(data)
                    await upstream_writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass

        # Run both directions concurrently; when either ends, cancel the other
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
    # Read headers until \r\n\r\n
    header_buf = bytearray()
    while True:
        byte = await reader.readexactly(1)
        header_buf.extend(byte)
        if header_buf[-4:] == b"\r\n\r\n":
            break

    # Check for Content-Length to read body
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
