#!/usr/bin/env python3
"""Verify the MJPEG camera proxy against a LIVE RTSP camera.

Why this tool exists: the MJPEG proxy's shipped argv **cannot be driven by a file
fixture at all**. `-fflags +nobuffer` disables input buffering, so the command
reads 0 bytes from a local file on every FFmpeg version (bisected 2026-10-15:
`+flush_packets`, `+discardcorrupt` and `+genpts` each yield 201,329 bytes;
`+nobuffer` alone yields 0). That is why the `-bsf:v` defect — which broke every
camera on this path from v1.4.0 (2026-04-07) to 2026-10-14 — survived ~20
releases and a unit suite: **only a live source reproduces it.**

This drives the REAL `DashieMjpegStreamView.get()` over real HTTP, using the
`?source=` query param so no Home Assistant instance is needed (that path never
dereferences `hass`). Nothing is reimplemented: the only injection is putting the
pre-fix `-bsf:v` pair back after `-i`, exactly where it sat.

    python3 tools/live_camera_mjpeg_check.py --source rtsp://<cam>:8554/

Needs the test venv (it imports `homeassistant.components.http`), e.g.
`~/.cache/dashie-ha-venvs/current/bin/python`.

Modes: (default) the 3 legs · `--race-rate N`.
For "does this camera emit SEI?" see live_camera_sei_check.py.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp import ClientSession, ClientTimeout, web  # noqa: E402

from custom_components.dashie import stream_proxy as sp  # noqa: E402

from _cred_safe import P as _P, read_source  # noqa: E402

_REAL_BUILD = sp._build_ffmpeg_cmd


class _Rec(logging.Handler):
    """Capture the proxy's own log records — the DROP: markers are the subject."""

    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []

    def emit(self, r: logging.LogRecord) -> None:
        self.out.append(r.getMessage())

    def drops(self) -> list[str]:
        return [m for m in self.out if "DROP:" in m]


def _inject_defect(cmd: list[str]) -> list[str]:
    """Put the pre-fix -bsf:v back immediately after `-i <src>`, where it shipped."""
    i = cmd.index("-i")
    return cmd[: i + 2] + ["-bsf:v", "filter_units=remove_types=6"] + cmd[i + 2 :]


async def _run_leg(name: str, src: str, *, break_calls: set[int],
                   want_frames: bool, max_seconds: float, read_frames: int = 8) -> dict:
    rec = _Rec()
    lg = logging.getLogger("custom_components.dashie.stream_proxy")
    lg.setLevel(logging.DEBUG)
    lg.addHandler(rec)
    calls = {"n": 0}

    def fake_build(*a, **kw):
        calls["n"] += 1
        cmd = _REAL_BUILD(*a, **kw)
        return _inject_defect(cmd) if calls["n"] in break_calls else cmd

    sp._build_ffmpeg_cmd = fake_build
    app = web.Application()
    app["hass"] = object()  # never dereferenced on the ?source= path
    view = sp.DashieMjpegStreamView()
    app.router.add_get(
        "/api/dashie/stream/mjpeg/{entity_id:.*}",
        lambda req: view.get(req, req.match_info["entity_id"]),
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]

    frames, status, t0 = 0, None, time.monotonic()
    try:
        async with ClientSession(timeout=ClientTimeout(total=max_seconds + 30)) as s:
            url = (f"http://127.0.0.1:{port}/api/dashie/stream/mjpeg/camera.harness"
                   f"?source={src}&fps=5&quality=20&width=640")
            async with s.get(url) as resp:
                status = resp.status
                while time.monotonic() - t0 < max_seconds:
                    chunk = await resp.content.read(65536)
                    if not chunk:
                        break  # server loop ended and closed the body
                    frames += chunk.count(b"\xff\xd8")
                    if frames >= read_frames:
                        break  # got what we came for; closing => client_gone
    finally:
        sp._build_ffmpeg_cmd = _REAL_BUILD
        await runner.cleanup()
        lg.removeHandler(rec)

    drops = rec.drops()
    r = {
        "name": name, "calls": calls["n"], "frames": frames, "status": status,
        "drops": drops,
        "gave_up": len([d for d in drops if "giving up" in d]),
        "first_loud": len([d for d in drops if "retrying up to" in d]),
        "frames_ok": (frames > 0) if want_frames else (frames == 0),
    }
    print(f"\n===== {name} =====")
    print(f"  http status        : {status}")
    print(f"  ffmpeg invocations : {calls['n']}")
    print(f"  JPEG frames to client: {frames}")
    print(f"  DROP records       : {len(drops)} "
          f"(first-loud={r['first_loud']}, gave-up={r['gave_up']})")
    for d in drops:
        _P(f"    | {d[:300]}")
    return r


async def _leg_until(name: str, src: str, verdicts, *, break_calls: set[int],
                     want_frames: bool, max_seconds: float, tries: int = 3) -> list:
    """Re-run a leg until its own verdicts hold, up to `tries` attempts.

    🔴 Why an instrument gets retries. The probe race is a MEASURED 7-12%
    transient (prove it yourself with --race-rate). A single-shot leg therefore
    reds about 1 run in 3 — measured 2026-10-16, 1 of 3 consecutive runs — for a
    reason that has nothing to do with the subject, and that red is
    INDISTINGUISHABLE from a real regression. An instrument that cries wolf at
    33% is worse than no instrument, because the next person learns to re-run it
    until it is green, which is how a real failure gets dismissed.
    A genuine breakage still fails every attempt, and the attempt count is
    always printed so a leg that needed 3 is never mistaken for a clean one.
    """
    for attempt in range(1, tries + 1):
        r = await _run_leg(name, src, break_calls=break_calls,
                           want_frames=want_frames, max_seconds=max_seconds)
        v = verdicts(r)
        if all(ok for _, ok in v):
            print(f"  -> verdicts hold on attempt {attempt}/{tries}")
            return v
        if attempt < tries:
            failed = [lbl for lbl, ok in v if not ok]
            print(f"  -> attempt {attempt}/{tries} did not hold ({'; '.join(failed)});"
                  f" retrying — the probe race is a known 7-12% transient")
    print(f"  -> STILL failing after {tries} attempts: this is not the probe race")
    return v


async def _legs(src: str) -> int:
    print(f"[preflight] module {sp.__file__}")
    print(f"[preflight] ZERO_FRAME_RETRIES={sp.ZERO_FRAME_RETRIES} "
          f"MAX_RECONNECTS={sp.MAX_RECONNECTS} RECONNECT_DELAY={sp.RECONNECT_DELAY}")
    if sp.ZERO_FRAME_RETRIES != 2:
        print("  !! constant moved — re-read its comment before trusting these legs")
    want = sp.ZERO_FRAME_RETRIES + 1

    all_v = []
    all_v += await _leg_until(
        "L1 shipping argv, live camera (positive control)", src,
        # "no DROP" is deliberately NOT a verdict. At the measured 7-12% probe-race
        # rate it fails that often BY DESIGN, and a verdict that is expected to fail
        # is not a verdict — it is noise that trains the reader to ignore reds. The
        # DROP count is printed above either way; production retrying is correct
        # behavior, not a defect, so L1 asserts only the outcome that must hold.
        lambda r: [
            ("L1 live camera delivers frames on the shipping argv", r["frames_ok"]),
        ],
        break_calls=set(), want_frames=True, max_seconds=25)

    # L2 is DETERMINISTIC — the argument is rejected every time — so it gets one
    # attempt. If this ever needs a retry, something has changed, not raced.
    all_v += await _leg_until(
        "L2 pre-fix argv re-injected (the shipped defect)", src,
        lambda r: [
            ("L2 delivers zero frames", r["frames_ok"]),
            (f"L2 attempts exactly {want} (1 + ZERO_FRAME_RETRIES)", r["calls"] == want),
            ("L2 is loud on the FIRST failure", r["first_loud"] == 1),
            ("L2 gives up loudly, exactly once", r["gave_up"] == 1),
            ("L2 stderr tail names the real cause (exit 234)",
             any("234" in d for d in r["drops"])),
        ],
        break_calls=set(range(1, 12)), want_frames=False, max_seconds=60, tries=1)

    all_v += await _leg_until(
        "L3 transient: attempt 1 sabotaged, then healthy", src,
        lambda r: [
            ("L3 RECOVERS: frames reach the client", r["frames_ok"]),
            ("L3 retried rather than giving up", r["calls"] > 1 and r["gave_up"] == 0),
            ("L3 was still loud about the first failure", r["first_loud"] == 1),
        ],
        break_calls={1}, want_frames=True, max_seconds=40)

    print("\n" + "=" * 62)
    for label, ok in all_v:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    allok = all(ok for _, ok in all_v)
    print("=" * 62)
    print("ALL PASS" if allok else "SOME FAILED")
    return 0 if allok else 1


async def _race_rate(src: str, n: int) -> int:
    """How often the shipping argv loses its own probe race — what the retry absorbs."""
    zero = 0
    for i in range(1, n + 1):
        cmd = _REAL_BUILD(src, 5, 20, "640", "software")
        p = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        frames, t0 = 0, time.monotonic()
        try:
            while time.monotonic() - t0 < 8.0:
                try:
                    chunk = await asyncio.wait_for(p.stdout.read(65536), timeout=8.0)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                frames += chunk.count(b"\xff\xd8")
                if frames >= 2:
                    break
        finally:
            try:
                p.kill()
            except ProcessLookupError:
                pass
            err = (await p.stderr.read()).decode(errors="replace")
            await p.wait()
        if frames == 0:
            zero += 1
        first = next((l for l in err.splitlines() if l.strip()), "")
        _P(f"  run {i:2d}: {'OK  ' if frames else 'ZERO'} frames={frames} "
           f"rc={p.returncode} {first[:120] if not frames else ''}")
        await asyncio.sleep(0.5)
    print(f"\nzero-frame runs: {zero}/{n} ({100.0 * zero / n:.0f}%); gives up after "
          f"{sp.ZERO_FRAME_RETRIES + 1} CONSECUTIVE zero-frame runs")
    print("⚠️  consecutive failures are NOT independent — do not raise this rate to a "
          "power and call it the give-up risk (see ZERO_FRAME_RETRIES).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--source-file", metavar="PATH",
                     help="file whose first line is the rtsp:// URL. USE THIS for any "
                          "camera with credentials in the URL (Reolink, Dahua): argv is "
                          "world-readable via ps, a file can be chmod 600")
    src.add_argument("--source", help="rtsp:// URL, for a camera with NO credentials")
    ap.add_argument("--race-rate", type=int, metavar="N",
                    help="instead of the legs, run the shipping argv N times")
    a = ap.parse_args()
    a.source = read_source(a.source_file, a.source)
    if a.source is None:
        return 2
    if a.race_rate:
        return asyncio.run(_race_rate(a.source, a.race_rate))
    return asyncio.run(_legs(a.source))


if __name__ == "__main__":
    sys.exit(main())
