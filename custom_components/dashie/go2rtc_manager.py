"""go2rtc manager for Dashie.

Detects an existing go2rtc instance (add-on, Frigate, standalone) or
starts a minimal subprocess on a non-conflicting port. Provides a
unified API for stream registration regardless of who runs go2rtc.

Detection order:
1. go2rtc add-on (API port 1984)
2. Frigate's bundled go2rtc (API port 1984 on Frigate container)
3. Standalone go2rtc on common ports
4. Our own subprocess (API port 11984, RTSP port 18554)
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import stat

import aiohttp

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Ports for our managed subprocess (high ports to avoid conflicts)
_MANAGED_API_PORT = 11984
_MANAGED_RTSP_PORT = 18554

# Where to store the go2rtc binary
_GO2RTC_BIN_DIR = "/config/custom_components/dashie/bin"
_GO2RTC_CONFIG_PATH = "/config/custom_components/dashie/bin/go2rtc.yaml"

# go2rtc release to download
_GO2RTC_VERSION = "1.9.14"


class Go2RtcManager:
    """Manages go2rtc detection, subprocess lifecycle, and stream registration."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._api_url: str | None = None  # e.g. http://host:port
        self._rtsp_port: int | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._managed = False  # True if we started the subprocess

    @property
    def api_url(self) -> str | None:
        return self._api_url

    @property
    def rtsp_port(self) -> int | None:
        return self._rtsp_port

    @property
    def is_available(self) -> bool:
        return self._api_url is not None

    async def detect(self) -> bool:
        """Detect an existing go2rtc instance. Returns True if found."""
        # Check common go2rtc API endpoints
        candidates = [
            ("http://localhost:1984", 8554, "go2rtc add-on/standalone"),
            ("http://127.0.0.1:1984", 8554, "go2rtc localhost"),
            # Frigate's go2rtc (inside Docker network)
            ("http://ccab4aaf-frigate:1984", 8554, "Frigate bundled"),
            ("http://frigate:1984", 8554, "Frigate (by name)"),
        ]
        for api_url, rtsp_port, label in candidates:
            if await self._check_api(api_url):
                self._api_url = api_url
                self._rtsp_port = rtsp_port
                _LOGGER.info("Found existing go2rtc: %s (%s)", api_url, label)
                return True
        return False

    async def ensure(self) -> bool:
        """Ensure go2rtc is available — detect or start subprocess."""
        if self._api_url:
            # Verify it's still alive
            if await self._check_api(self._api_url):
                return True
            # Lost connection — reset
            self._api_url = None
            self._rtsp_port = None

        # Try to detect existing instance
        if await self.detect():
            return True

        # Check if our managed instance is already running
        managed_url = f"http://localhost:{_MANAGED_API_PORT}"
        if await self._check_api(managed_url):
            self._api_url = managed_url
            self._rtsp_port = _MANAGED_RTSP_PORT
            self._managed = True
            _LOGGER.info("Found managed go2rtc on port %d", _MANAGED_API_PORT)
            return True

        # Start our own subprocess
        return await self._start_subprocess()

    async def register_stream(self, name: str, upstream_url: str) -> str | None:
        """Register a stream in go2rtc and return the RTSP URL.

        Returns rtsp://host:port/name or None on failure.
        """
        if not self._api_url:
            return None

        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Check if stream already exists
                async with session.get(f"{self._api_url}/api/streams") as resp:
                    if resp.status == 200:
                        streams = await resp.json()
                        if name in streams:
                            # Already registered
                            return self._build_rtsp_url(name)

                # Register new stream via PUT
                async with session.put(
                    f"{self._api_url}/api/streams",
                    params={"name": name, "src": upstream_url},
                ) as resp:
                    if resp.status in (200, 201):
                        _LOGGER.info("Registered go2rtc stream: %s", name)
                        return self._build_rtsp_url(name)
                    _LOGGER.warning(
                        "Failed to register go2rtc stream %s: HTTP %d",
                        name, resp.status,
                    )
        except Exception as e:
            _LOGGER.warning("go2rtc stream registration failed for %s: %s", name, e)
        return None

    async def has_stream(self, name: str) -> tuple[bool, str | None]:
        """Check if a stream exists in go2rtc.

        Returns (exists, rtsp_url) tuple.
        """
        if not self._api_url:
            return False, None
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self._api_url}/api/streams") as resp:
                    if resp.status == 200:
                        streams = await resp.json()
                        if name in streams:
                            return True, self._build_rtsp_url(name)
        except Exception:
            pass
        return False, None

    async def shutdown(self) -> None:
        """Stop managed subprocess if we started it."""
        if self._process and self._managed:
            _LOGGER.info("Stopping managed go2rtc subprocess")
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._process.kill()
            self._process = None
        self._api_url = None
        self._rtsp_port = None
        self._managed = False

    def _build_rtsp_url(self, name: str) -> str:
        """Build RTSP URL for a registered stream."""
        # Use the HA host IP, not localhost (tablets connect remotely)
        return f"rtsp://{{ha_ip}}:{self._rtsp_port}/{name}"

    async def _check_api(self, api_url: str) -> bool:
        """Check if a go2rtc API endpoint is responding."""
        try:
            timeout = aiohttp.ClientTimeout(total=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{api_url}/api/streams") as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def _start_subprocess(self) -> bool:
        """Download go2rtc binary if needed and start as subprocess."""
        binary = await self._ensure_binary()
        if not binary:
            _LOGGER.warning("Could not obtain go2rtc binary — RTSP relay unavailable")
            return False

        # Write minimal config
        config = (
            f"api:\n"
            f"  listen: \":{_MANAGED_API_PORT}\"\n"
            f"rtsp:\n"
            f"  listen: \":{_MANAGED_RTSP_PORT}\"\n"
            f"streams: {{}}\n"
        )
        os.makedirs(os.path.dirname(_GO2RTC_CONFIG_PATH), exist_ok=True)
        with open(_GO2RTC_CONFIG_PATH, "w") as f:
            f.write(config)

        _LOGGER.info("Starting managed go2rtc on API=%d RTSP=%d", _MANAGED_API_PORT, _MANAGED_RTSP_PORT)
        try:
            self._process = await asyncio.create_subprocess_exec(
                binary, "-config", _GO2RTC_CONFIG_PATH,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            # Wait a moment for it to start
            await asyncio.sleep(2)

            if self._process.returncode is not None:
                _LOGGER.error("go2rtc subprocess exited immediately with code %d", self._process.returncode)
                return False

            managed_url = f"http://localhost:{_MANAGED_API_PORT}"
            if await self._check_api(managed_url):
                self._api_url = managed_url
                self._rtsp_port = _MANAGED_RTSP_PORT
                self._managed = True
                _LOGGER.info("Managed go2rtc started successfully")
                return True
            else:
                _LOGGER.error("go2rtc started but API not responding")
                return False
        except Exception as e:
            _LOGGER.error("Failed to start go2rtc subprocess: %s", e)
            return False

    async def _ensure_binary(self) -> str | None:
        """Ensure go2rtc binary exists, download if needed. Returns path or None."""
        os.makedirs(_GO2RTC_BIN_DIR, exist_ok=True)
        binary_path = os.path.join(_GO2RTC_BIN_DIR, "go2rtc")

        if os.path.isfile(binary_path) and os.access(binary_path, os.X_OK):
            return binary_path

        # Determine architecture
        machine = platform.machine().lower()
        if machine in ("aarch64", "arm64"):
            arch = "arm64"
        elif machine in ("x86_64", "amd64"):
            arch = "amd64"
        elif machine.startswith("arm"):
            arch = "armv6"
        else:
            _LOGGER.warning("Unsupported architecture for go2rtc: %s", machine)
            return None

        url = (
            f"https://github.com/AlexxIT/go2rtc/releases/download/"
            f"v{_GO2RTC_VERSION}/go2rtc_linux_{arch}"
        )
        _LOGGER.info("Downloading go2rtc v%s for %s...", _GO2RTC_VERSION, arch)

        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        _LOGGER.error("Failed to download go2rtc: HTTP %d", resp.status)
                        return None
                    data = await resp.read()
                    with open(binary_path, "wb") as f:
                        f.write(data)
                    # Make executable
                    os.chmod(binary_path, os.stat(binary_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                    _LOGGER.info("Downloaded go2rtc to %s (%d bytes)", binary_path, len(data))
                    return binary_path
        except Exception as e:
            _LOGGER.error("Failed to download go2rtc: %s", e)
            return None
