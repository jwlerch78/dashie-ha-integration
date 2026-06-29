#!/usr/bin/env python3
"""Fake Dashie device — a fault-injecting simulator for testing the integration.

Serves the device HTTP API (cmd=deviceInfo, getRtspStatus, commands, …) and
advertises itself over mDNS as `_dashie-kiosk._tcp.local.`, so a real Home
Assistant discovers it like a physical Dashie tablet. Fault flags let you
reproduce the failure modes we hit in the field without any hardware:

  # Healthy device (discovers + adds + entities populate)
  python3 fake_dashie_device.py

  # Slow device (~12s responses) — reproduces the poll-timeout asymmetry that
  # left entities "unavailable" (config flow 15s adds it; old 10s poll cap failed)
  python3 fake_dashie_device.py --delay 12

  # First poll(s) hang then recover — reproduces the add→"Success"→vanish loop
  python3 fake_dashie_device.py --fail-first 1

  # Flaps in and out — reproduces entities going available/unavailable
  python3 fake_dashie_device.py --down-cycle 30/15

  # Advertise IPv6 alongside IPv4 — reproduces the dual-stack discovery case
  python3 fake_dashie_device.py --ipv6 fc00::1234

Requires `zeroconf` for mDNS discovery (pip install zeroconf); without it the
server still runs and you can add it manually by IP in HA.
"""
from __future__ import annotations

import argparse
import json
import random
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

START = time.monotonic()
_deviceinfo_requests = 0
_lock = threading.Lock()
ARGS: argparse.Namespace


def _local_ipv4() -> str:
    """Best-effort local IPv4 (the address HA will reach us on)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _device_info() -> dict:
    """A realistic deviceInfo payload (subset the integration reads)."""
    return {
        "deviceID": ARGS.device_id,
        "stableDeviceID": ARGS.device_id,
        "deviceName": ARGS.name,
        "deviceModel": "Fake Echo Show 5",
        "deviceManufacturer": "DashieSim",
        "androidVersion": "11",
        "batteryLevel": 100,
        "plugged": True,
        "isScreenOn": True,
        "screenOn": True,
        "screenBrightness": 178,
        "kioskMode": True,
        "isDarkMode": True,
        "audioVolume": 50,
        "currentVolume": 50,
        "audioMuted": False,
        "rtspEnabled": False,
        "ip4": ARGS.host_ip,
        "appVersionName": "fake-1.0",
        "appVersionCode": 1,
        "isLicensed": True,
        "rtspConfig": {"width": 1280, "height": 720, "fps": 15, "port": 8554},
        "settings": {"screenBrightness": 178, "mqttEnabled": False},
    }


def _should_hang() -> bool:
    """Decide whether this request should hang (simulating a stalled device)."""
    global _deviceinfo_requests
    with _lock:
        _deviceinfo_requests += 1
        n = _deviceinfo_requests
    if ARGS.fail_first and n <= ARGS.fail_first:
        return True
    if ARGS.fail_rate and random.random() < ARGS.fail_rate:
        return True
    if ARGS.down_cycle:
        up, down = ARGS.down_cycle
        if (time.monotonic() - START) % (up + down) >= up:
            return True
    return False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):  # quieter, with our own prefix
        print(f"[fake-device] {self.address_string()} {fmt % a}")

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        cmd = (qs.get("cmd") or [""])[0]

        # Fault injection (applies to the status-bearing deviceInfo poll).
        if cmd == "deviceInfo" and _should_hang():
            print(f"[fake-device] HANGING deviceInfo for {ARGS.hang}s (fault injected)")
            time.sleep(ARGS.hang)  # client will hit its read timeout first
        elif ARGS.delay:
            time.sleep(ARGS.delay)

        if cmd == "deviceInfo":
            return self._json(_device_info())
        if cmd == "getRtspStatus":
            return self._json({"running": False, "clients": 0})
        if cmd == "getRtspConfig":
            return self._json({"width": 1280, "height": 720, "fps": 15, "port": 8554})
        # Any control command (screenOn, setBrightness, …) → OK.
        return self._json({"status": "OK", "cmd": cmd})

    def _json(self, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _register_mdns(ip: str):
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        print("[fake-device] zeroconf not installed — skipping mDNS "
              "(pip install zeroconf to enable auto-discovery)")
        return None

    addresses = []
    if ARGS.ipv6:
        addresses.append(socket.inet_pton(socket.AF_INET6, ARGS.ipv6))  # IPv6 first
    addresses.append(socket.inet_aton(ip))

    info = ServiceInfo(
        "_dashie-kiosk._tcp.local.",
        f"{ARGS.name}._dashie-kiosk._tcp.local.",
        addresses=addresses,
        port=ARGS.port,
        properties={
            "name": ARGS.name,
            "uuid": ARGS.device_id,
            "version": "fake-1.0",
            "api_port": str(ARGS.port),
            "ha_url": ARGS.ha_url,
        },
        server=f"fake-dashie-{ARGS.port}.local.",
    )
    zc = Zeroconf()
    zc.register_service(info)
    print(f"[fake-device] mDNS registered: {ARGS.name} _dashie-kiosk._tcp "
          f"@ {'[' + ARGS.ipv6 + '], ' if ARGS.ipv6 else ''}{ip}:{ARGS.port}")
    return zc


def main():
    global ARGS
    p = argparse.ArgumentParser(description="Fault-injecting fake Dashie device")
    p.add_argument("--port", type=int, default=2323)
    p.add_argument("--host-ip", default=None, help="IPv4 to advertise (default: auto-detect)")
    p.add_argument("--device-id", default="fakedevice00000000000000000000aa")
    p.add_argument("--name", default="Fake Dashie Device")
    p.add_argument("--ha-url", default="http://homeassistant.local:8123/", help="TXT ha_url")
    p.add_argument("--delay", type=float, default=0, help="seconds of latency on every response")
    p.add_argument("--hang", type=float, default=60, help="seconds a 'failed' request hangs")
    p.add_argument("--fail-first", type=int, default=0, help="hang the first N deviceInfo polls, then recover")
    p.add_argument("--fail-rate", type=float, default=0, help="randomly hang this fraction of polls (0-1)")
    p.add_argument("--down-cycle", default=None, help="UP/DOWN seconds, e.g. 30/15 — alternate healthy/hung windows")
    p.add_argument("--ipv6", default=None, help="also advertise this IPv6 address (first), to test dual-stack discovery")
    p.add_argument("--no-mdns", action="store_true", help="serve HTTP only; add manually by IP in HA")
    ARGS = p.parse_args()

    if ARGS.down_cycle:
        up, down = ARGS.down_cycle.split("/")
        ARGS.down_cycle = (float(up), float(down))
    if not ARGS.host_ip:
        ARGS.host_ip = _local_ipv4()

    zc = None if ARGS.no_mdns else _register_mdns(ARGS.host_ip)

    server = ThreadingHTTPServer(("0.0.0.0", ARGS.port), Handler)
    print(f"[fake-device] serving Dashie API on http://{ARGS.host_ip}:{ARGS.port}/?cmd=deviceInfo&type=json")
    faults = [f for f in (
        f"delay={ARGS.delay}s" if ARGS.delay else "",
        f"fail-first={ARGS.fail_first}" if ARGS.fail_first else "",
        f"fail-rate={ARGS.fail_rate}" if ARGS.fail_rate else "",
        f"down-cycle={ARGS.down_cycle}" if ARGS.down_cycle else "",
    ) if f]
    print(f"[fake-device] faults: {', '.join(faults) if faults else 'none (healthy)'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[fake-device] shutting down")
    finally:
        if zc:
            zc.close()
        server.shutdown()


if __name__ == "__main__":
    main()
