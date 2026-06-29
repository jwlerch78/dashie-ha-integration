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
