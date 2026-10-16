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
# camera with credentials in the URL (Reolink, Dahua) — ALWAYS use a file
printf 'rtsp://user:pass@cam:554/h264Preview_01_main\n' > ~/.cam-url
chmod 600 ~/.cam-url
~/.cache/dashie-ha-venvs/current/bin/python \
    tools/live_camera_mjpeg_check.py --source-file ~/.cam-url

# camera with no credentials
~/.cache/dashie-ha-venvs/current/bin/python \
    tools/live_camera_mjpeg_check.py --source rtsp://cam:8554/
```

🔴 **`--source-file`, not `--source`, for any camera whose URL carries
credentials** — argv is world-readable through `ps`. The tool refuses a file that
is not mode 600, and every line it prints goes through `_redact_url`, so the
password reaches neither the terminal nor a transcript. Verified with a canary
password: 0 occurrences across a full legs run, including the `DROP:` stderr
tails, which is where it would otherwise land. (Making this safe is what exposed
the query-string hole in `_redact_url` — see `tests/test_redact_url.py`.)

| Leg | What it proves |
|---|---|
| **L1** | the shipping argv delivers frames from a live camera |
| **L2** | the pre-fix argv is rejected: zero frames, loud once, bounded to `1 + ZERO_FRAME_RETRIES` attempts, then gives up loudly |
| **L3** | **a transient zero-frame exit RECOVERS** — the leg that matters on review |

`--race-rate N` measures how often the argv loses its own probe race, which is
what the retry absorbs.

⚠️ **Why the legs retry themselves.** The probe race is a measured **7-12%**
transient, so a single-shot leg reds about **1 run in 3** (measured: 1 of 3
consecutive runs) for a reason that has nothing to do with the subject — and that
red is indistinguishable from a real regression. Each leg therefore re-runs up to
3 times and **always prints which attempt it held on**, so a leg that needed 3 is
never mistaken for a clean one. A genuine breakage still fails all 3. L2 is
deterministic and gets one attempt by design.

L3 is the leg that matters: the first fix for the `-bsf:v` defect made a
zero-frame exit non-retrying, which would have made a *flaky* camera give up.
Only a live camera showed that.

**Measured 2026-10-16, Samsung tablet RTSP server, HA 2026.9.3 / FFmpeg 9.0.2:**
all verdicts PASS · probe-race rate 1/14 (7%) · SEI NAL units **0**.

## `live_camera_sei_check.py` — does this camera emit SEI?

The question the deleted `-bsf:v` was *meant* to answer. Characterizes a camera
rather than testing the proxy, to find a source that can say whether stripping
SEI was ever worth doing.

```bash
~/.cache/dashie-ha-venvs/current/bin/python \
    tools/live_camera_sei_check.py --source-file ~/.cam-url
```

Same credential handling as above. It **always runs a positive control** — an
x264 clip that definitely contains SEI — because a zero from a detector never
shown able to report non-zero is a non-answer. The first attempt at that control
silently produced no file at all, which would have made the camera's zero look
like an answer.

Measured: John's Samsung RTSP server emits **0** SEI NAL units (0 of 219 over
12 s / 3.5 MB) ⇒ it cannot answer the question. A Reolink is the next candidate.
