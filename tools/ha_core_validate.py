"""End-to-end validation of the Dashie integration inside a REAL HA Core instance.

Why this exists: everything else is a unit test or a harness that bypassed HA. The
camera harness registered DashieMjpegStreamView on a plain aiohttp app, so
`requires_auth = True` and `register_stream_proxy_views()` were NEVER executed, and
nothing had ever answered "does this integration load inside Home Assistant".

Takes a FRESH config dir as argv[1]: onboarding can create the owner only once, so
reusing a dir makes run 2 fail for a reason unrelated to the subject. Nothing here
deletes anything.

    # one-time: build the HA Core venv (any HA version you want to validate against)
    python3 -m venv /tmp/ha-core/.venv
    /tmp/ha-core/.venv/bin/pip install "homeassistant==2026.9.3"

    # each run: a FRESH config dir
    HA_CORE_ROOT=/tmp/ha-core /tmp/ha-core/.venv/bin/python \
        tools/ha_core_validate.py /tmp/ha-core/run-$(date +%s)

Measured 2026-10-16 against HA Core 2026.9.3, integration d72b00b: 18/18.

🔴 Two of its checks were WRONG on the first run and passing systems failed them.
Both are recorded in the code, because each is a trap that will recur:
  * it asserted 401 on an MJPEG probe BEFORE any config entry existed. The views are
    registered by async_setup_entry, so 404 was correct — a missing precondition in
    the instrument wearing the costume of a product bug.
  * it treated any 404 as "route not registered". The view answers 404 with its own
    JSON error when the camera entity is absent; the STATUS cannot tell those apart,
    only the BODY can.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# The integration tree is the repo THIS FILE lives in, so the harness always
# validates the checkout it shipped with rather than a path someone forgot to update.
REPO = Path(__file__).resolve().parent.parent
HA_VENV = Path(os.environ.get("HA_CORE_VENV", "")) if os.environ.get("HA_CORE_VENV") else None
ROOT = HA_VENV.parent if HA_VENV else Path(os.environ.get("HA_CORE_ROOT", REPO / ".ha-core"))
WT = REPO
CONFIG = Path(sys.argv[1]).resolve()
PORT = int(os.environ.get("HA_CORE_PORT", "8129"))
BASE = f"http://127.0.0.1:{PORT}"
CLIENT_ID = f"{BASE}/"
LOG = CONFIG / "ha.log"

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    sys.stdout.flush()


def note(msg: str) -> None:
    print(f"  [observation] {msg}")
    sys.stdout.flush()


def http(path, data=None, token=None, form=False):
    url = path if path.startswith("http") else BASE + path
    body, headers = None, {}
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def prepare_config() -> None:
    CONFIG.mkdir(parents=True, exist_ok=True)
    (CONFIG / "custom_components").mkdir(exist_ok=True)
    (CONFIG / "media").mkdir(exist_ok=True)
    link = CONFIG / "custom_components" / "dashie"
    if not link.exists():
        link.symlink_to(WT / "custom_components" / "dashie")
    (CONFIG / "configuration.yaml").write_text(
        "# Minimal on purpose: the integration's declared dependencies and little\n"
        "# else, so a failure points at the integration, not at default_config.\n"
        "homeassistant:\n"
        "  name: DashieValidation\n"
        "  media_dirs:\n"
        f"    media: {CONFIG / 'media'}\n"
        "\nhttp:\n"
        f"  server_port: {PORT}\n"
        "\nlogger:\n"
        "  default: warning\n"
        "  logs:\n"
        "    custom_components.dashie: debug\n"
        "\nstream:\nconversation:\n"
    )


def wait_for_api(timeout: float) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        status, _ = http("/")
        if status in (200, 401, 404):
            return True
        time.sleep(2)
    return False


def main() -> int:
    prepare_config()
    print(f"config: {CONFIG}")
    print(f"integration: {(CONFIG / 'custom_components' / 'dashie').resolve()}")
    print("=" * 70)
    print("PHASE 1 — does HA start, and does the integration LOAD?")
    print("=" * 70)
    hass = subprocess.Popen(
        [str(ROOT / ".venv/bin/hass"), "-c", str(CONFIG), "--log-file", str(LOG)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fake = None
    try:
        up = wait_for_api(600)
        record("HA Core starts and serves HTTP", up, f"port {PORT}" if up else "no HTTP in 600s")
        if not up:
            return 1
        text = LOG.read_text(errors="replace") if LOG.exists() else ""
        fatal = [ln for ln in text.splitlines() if "dashie" in ln.lower() and any(
            k in ln for k in ("Setup failed", "Error during setup", "Unable to prepare setup",
                              "Error importing platform", "Traceback", "ImportError",
                              "SyntaxError", "Unable to import"))]
        record("integration imports with no fatal log line", not fatal,
               fatal[0][:150] if fatal else "")
        record("HA actually saw the integration", "dashie" in text.lower(),
               "" if "dashie" in text.lower() else "no mention of 'dashie' in the log")

        print("\n" + "=" * 70)
        print("PHASE 2 — a real auth token, so views are tested as HA serves them")
        print("=" * 70)
        status, body = http("/api/onboarding/users", {
            "client_id": CLIENT_ID, "name": "Validator", "username": "validator",
            "password": "validator-pw-0001", "language": "en"})
        code = body.get("auth_code") if isinstance(body, dict) else None
        record("onboarding creates the owner user", bool(code), f"HTTP {status}")
        if not code:
            return 1
        status, body = http("/auth/token", {
            "grant_type": "authorization_code", "code": code, "client_id": CLIENT_ID}, form=True)
        token = body.get("access_token") if isinstance(body, dict) else None
        record("access token issued", bool(token), f"HTTP {status}")
        if not token:
            return 1
        status, _ = http("/api/", token=token)
        record("authenticated API reachable", status == 200, f"HTTP {status}")

        probe = "/api/dashie/stream/mjpeg/camera.nonexistent"
        s_pre, _ = http(probe)
        note(f"before any config entry, {probe} -> HTTP {s_pre} "
             + ("(expected — the views are entry-scoped, registered by "
                "async_setup_entry at __init__.py:275)" if s_pre == 404 else "(UNEXPECTED)"))
        note("run 1 of this script asserted 401 here and 'failed' — a missing "
             "precondition in the instrument, wearing the costume of a product bug")

        print("\n" + "=" * 70)
        print("PHASE 2.5 — a real config entry, via the real flow + the repo's fake device")
        print("=" * 70)
        fake = subprocess.Popen(
            [str(ROOT / ".venv/bin/python"), str(WT / "tools/fake_dashie_device.py"),
             "--no-mdns", "--port", "2323"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        serving = False
        for _ in range(20):
            st, _b = http("http://127.0.0.1:2323/?cmd=getDeviceInfo")
            if st == 200:
                serving = True
                break
            time.sleep(1)
        record("fake Dashie device serving on :2323", serving)

        status, body = http("/api/config/config_entries/flow",
                            {"handler": "dashie", "show_advanced_options": False}, token=token)
        flow_id = body.get("flow_id") if isinstance(body, dict) else None
        record("config flow starts", bool(flow_id), f"HTTP {status}")
        entry_ok = False
        if flow_id:
            status, body = http(f"/api/config/config_entries/flow/{flow_id}",
                                {"host": "127.0.0.1", "port": 2323}, token=token)
            ty = body.get("type") if isinstance(body, dict) else None
            entry_ok = ty == "create_entry"
            record("config entry created from the user step", entry_ok,
                   f"HTTP {status} type={ty} "
                   + (str(body.get("errors") or "")[:80] if isinstance(body, dict) else ""))
        if entry_ok:
            time.sleep(15)

        print("\n" + "=" * 70)
        print("PHASE 3 — are the Dashie HTTP views REGISTERED and AUTH-GATED?")
        print("   401 = registered and gated. 404 = never registered. That is the")
        print("   discriminator my camera harness structurally could not see.")
        print("=" * 70)
        s_noauth, _ = http(probe)
        record("MJPEG view rejects an unauthenticated request (401, not 404)",
               s_noauth == 401,
               f"HTTP {s_noauth}" + (" — 404: the view never registered" if s_noauth == 404 else ""))
        s_auth, b_auth = http(probe, token=token)
        # ⚠️ A 404 here can mean two opposite things and the STATUS cannot tell them
        # apart: route-not-registered, or the view ran and reported that the camera
        # entity does not exist. The BODY discriminates — the view answers JSON with
        # its own error text. Asserting `!= 404` conflated them and failed a passing
        # system, so assert the view EXECUTED instead.
        ran = isinstance(b_auth, dict) and "camera" in str(b_auth.get("error", "")).lower()
        record("MJPEG view EXECUTES with a token (its own error, not a routing 404)",
               ran, f"HTTP {s_auth} {str(b_auth)[:70]}")
        st2, _ = http("/api/dashie/stream/snapshot/camera.nonexistent")
        record("snapshot view registered and gated (401)", st2 == 401, f"HTTP {st2}")

        print("\n" + "=" * 70)
        print("PHASE 4 — do D-281 items 4 and 6 appear as real entities?")
        print("=" * 70)
        status, states = http("/api/states", token=token)
        ids = [e.get("entity_id", "") for e in states] if isinstance(states, list) else []
        record("entity states readable", bool(ids), f"HTTP {status}, {len(ids)} entities")
        for label, needle in (("Device Owner (item 4)", "device_owner"),
                              ("Package Name (item 6)", "package_name")):
            hit = [i for i in ids if needle in i]
            record(f"{label} exists in HA", bool(hit), hit[0] if hit else "not in /api/states")
        # Narrow: the earlier check matched `button.*_bring_to_foreground`, a
        # pre-existing and correctly-named entity. The decision being pinned is only
        # that the PACKAGE NAME sensor is not called foreground_app.
        bad = [i for i in ids if "foreground_app" in i.lower()]
        record("the package-name sensor is not exposed as 'foreground_app'",
               not bad, str(bad[:2]))
        note("for contrast, these legitimately contain 'foreground': "
             + str([i for i in ids if "foreground" in i.lower()][:3]))

        print("\n" + "=" * 70)
        print("PHASE 5 — does a Supervisor-less install reproduce John's two log lines?")
        print("   HA Core has no Supervisor and no add-on store, so the add-on CANNOT")
        print("   be installed here. Anything warned about its absence is a false alarm.")
        print("=" * 70)
        log = LOG.read_text(errors="replace") if LOG.exists() else ""
        lines = log.splitlines()
        secret_warn = [l for l in lines if "No bridge secret found" in l]
        kiosk_warn = [l for l in lines if "Kiosk authorize refused" in l]
        skip_dbg = [l for l in lines if "skipping add-on discovery" in l]
        note(f"'No bridge secret found' WARNINGs: {len(secret_warn)}")
        if secret_warn:
            note("  -> " + secret_warn[0][-150:])
        note(f"'Kiosk authorize refused' WARNINGs: {len(kiosk_warn)}")
        note(f"'skipping add-on discovery' (debug, correct): {len(skip_dbg)}")
        warn_count = len([l for l in lines
                          if "WARNING" in l and "custom_components.dashie" in l])
        note(f"total dashie WARNING lines on a clean Supervisor-less start: {warn_count}")
        for l in [l for l in lines if "WARNING" in l and "custom_components.dashie" in l][:6]:
            note("  W: " + l[-150:])

        print("\n" + "=" * 70)
        print("PHASE 6 — can John's two log lines be REPRODUCED, and by what?")
        print("   Phase 5 found ZERO of them on a clean start, which refutes the idea")
        print("   that they are unconditional on a Supervisor-less box. _bridge_headers")
        print("   is LAZY — it runs only when something actually calls the add-on. So")
        print("   drive the kiosk-authorize endpoint and see if both lines appear.")
        print("=" * 70)
        before = len(lines)
        st6, b6 = http("/api/dashie/account/authorize",
                       {"user_code": "TESTCODE"}, token=token)
        note(f"POST /api/dashie/account/authorize -> HTTP {st6} {str(b6)[:90]}")
        time.sleep(3)
        after = (LOG.read_text(errors="replace") if LOG.exists() else "").splitlines()
        fresh = after[before:]
        got_secret = [l for l in fresh if "No bridge secret found" in l]
        got_kiosk = [l for l in fresh if "Kiosk authorize refused" in l]
        record("reproduces 'No bridge secret found'", bool(got_secret),
               got_secret[0][-110:] if got_secret else "did not appear")
        record("reproduces 'Kiosk authorize refused'", bool(got_kiosk),
               got_kiosk[0][-110:] if got_kiosk else "did not appear")
        note("=> if both reproduce, John's pair is NOT a startup artifact: something "
             "on his box ASKED for a kiosk authorize, and the add-on could not serve it")
    finally:
        for p in (fake, hass):
            if p is None:
                continue
            p.terminate()
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()

    print("\n" + "=" * 70)
    bad = [r for r in results if not r[1]]
    print(f"{len(results) - len(bad)}/{len(results)} passed")
    print("ALL PASS" if not bad else "SOME FAILED: " + "; ".join(r[0] for r in bad))
    return 0 if not bad else 1


sys.exit(main())
