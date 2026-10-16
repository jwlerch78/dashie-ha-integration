# Dashie integration test tools

## `fake_dashie_device.py` — fault-injecting device simulator

A fake Dashie device for testing the integration end-to-end without hardware.
It serves the device HTTP API and advertises itself over mDNS, so a real Home
Assistant **discovers it like a physical tablet** — then fault flags let you
reproduce the failure modes we've hit in the field on demand.

### Setup
```bash
pip install zeroconf        # only needed for mDNS auto-discovery
python3 tools/fake_dashie_device.py
```
Run it on the same LAN as Home Assistant. It auto-detects your IPv4 and
advertises `_dashie-kiosk._tcp.local.`; HA will offer it under
**Settings → Devices & Services**. (It also shows up alongside your real
devices, so give it a distinct `--name`.)

### Reproducing each scenario

| Scenario (and the release that fixed it) | Command |
|---|---|
| Healthy device — discovers, adds, entities populate | `fake_dashie_device.py` |
| **Slow device** → entities stuck "unavailable" (poll-timeout asymmetry, 1.4.12 / 1.4.14) | `--delay 12` |
| **First poll fails then recovers** → add→"Success"→vanish loop (1.4.13) | `--fail-first 1` |
| **Flapping** available/unavailable | `--down-cycle 30/15` |
| **Dual-stack** discovery, IPv6 advertised first (1.4.11) | `--ipv6 fc00::1234` |
| Intermittent failures | `--fail-rate 0.5` |

`--delay` adds latency to every response; a "failed"/"down" request **hangs**
(`--hang`, default 60s) so the client hits its read timeout — matching how a
real memory-stalled device behaves (TCP connects, app thread can't answer).

### Useful combos
```bash
# Reproduce Mat's exact saga end-to-end:
#  add succeeds (config flow 15s), entities then time out on the old 10s poll cap.
python3 tools/fake_dashie_device.py --name "Mat Echo" --delay 12

# Reproduce the add-loop on pre-1.4.13 integration builds:
python3 tools/fake_dashie_device.py --name "Loopy" --fail-first 2
```

Flags: `--port` (default 2323), `--host-ip`, `--device-id`, `--name`,
`--ha-url`, `--delay`, `--hang`, `--fail-first`, `--fail-rate`,
`--down-cycle UP/DOWN`, `--ipv6 <addr>`, `--no-mdns`.

## `live_camera_mjpeg_check.py` — MJPEG proxy against a real camera

The MJPEG proxy's shipped argv **cannot be driven by a file fixture**:
`-fflags +nobuffer` disables input buffering, so the command reads 0 bytes from a
local file on every FFmpeg version. That is why the `-bsf:v` defect (every camera
on this path broken from v1.4.0 to 2026-10-14) survived ~20 releases and a green
unit suite — **only a live RTSP source reproduces it.** Unit tests pin the argv;
this tool is the only thing that proves the behavior.

It drives the real `DashieMjpegStreamView.get()` over real HTTP via `?source=`,
so **no Home Assistant instance is needed**. Run it with the test venv:

```bash
~/.cache/dashie-ha-venvs/current/bin/python \
    tools/live_camera_mjpeg_check.py --source rtsp://<camera>:8554/
```

| Mode | What it answers |
|---|---|
| (default) 3 legs | L1 the shipping argv delivers frames · L2 the pre-fix argv is rejected, loud once, bounded to `1 + ZERO_FRAME_RETRIES` attempts, then gives up loudly · **L3 a transient zero-frame exit RECOVERS** |
| `--race-rate N` | how often the argv loses its own probe race (the thing the retry absorbs) |
| `--sei` | whether the camera emits SEI (NAL type 6) — with a positive control, because a zero from a detector never shown able to report non-zero is a non-answer |

L3 is the leg that matters on review: the first fix for the `-bsf:v` defect made a
zero-frame exit non-retrying, which would have made a *flaky* camera give up. Only
a live camera showed that.

**Measured 2026-10-16, Samsung tablet RTSP server, HA 2026.9.3 / FFmpeg 9.0.2:**
all 10 verdicts PASS · probe-race rate 1/14 (7%) · **SEI NAL units: 0**, so this
source cannot answer whether stripping SEI ever mattered.
