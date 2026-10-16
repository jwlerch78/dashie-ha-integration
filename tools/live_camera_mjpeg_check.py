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

Modes: (default) the 3 legs · `--race-rate N` · `--sei`.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import logging
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp import ClientSession, ClientTimeout, web  # noqa: E402

from custom_components.dashie import stream_proxy as sp  # noqa: E402

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
        print(f"    | {d[:300]}")
    return r


async def _legs(src: str) -> int:
    print(f"[preflight] module {sp.__file__}")
    print(f"[preflight] ZERO_FRAME_RETRIES={sp.ZERO_FRAME_RETRIES} "
          f"MAX_RECONNECTS={sp.MAX_RECONNECTS} RECONNECT_DELAY={sp.RECONNECT_DELAY}")
    if sp.ZERO_FRAME_RETRIES != 2:
        print("  !! constant moved — re-read its comment before trusting these legs")

    r1 = await _run_leg("L1 shipping argv, live camera (positive control)",
                        src, break_calls=set(), want_frames=True, max_seconds=25)
    r2 = await _run_leg("L2 pre-fix argv re-injected (the shipped defect)",
                        src, break_calls=set(range(1, 12)), want_frames=False,
                        max_seconds=60)
    r3 = await _run_leg("L3 transient: attempt 1 sabotaged, then healthy",
                        src, break_calls={1}, want_frames=True, max_seconds=40)

    want = sp.ZERO_FRAME_RETRIES + 1
    v = [
        ("L1 live camera delivers frames on the shipping argv", r1["frames_ok"]),
        ("L1 logs no DROP", not r1["drops"]),
        ("L2 delivers zero frames", r2["frames_ok"]),
        (f"L2 attempts exactly {want} (1 + ZERO_FRAME_RETRIES)", r2["calls"] == want),
        ("L2 is loud on the FIRST failure", r2["first_loud"] == 1),
        ("L2 gives up loudly, exactly once", r2["gave_up"] == 1),
        ("L2 stderr tail names the real cause (exit 234)",
         any("234" in d for d in r2["drops"])),
        ("L3 RECOVERS: frames reach the client", r3["frames_ok"]),
        ("L3 retried rather than giving up", r3["calls"] > 1 and r3["gave_up"] == 0),
        ("L3 was still loud about the first failure", r3["first_loud"] == 1),
    ]
    print("\n" + "=" * 62)
    for label, ok in v:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    allok = all(ok for _, ok in v)
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
        print(f"  run {i:2d}: {'OK  ' if frames else 'ZERO'} frames={frames} "
              f"rc={p.returncode} {first[:120] if not frames else ''}")
        await asyncio.sleep(0.5)
    print(f"\nzero-frame runs: {zero}/{n} ({100.0 * zero / n:.0f}%); gives up after "
          f"{sp.ZERO_FRAME_RETRIES + 1} CONSECUTIVE zero-frame runs")
    print("⚠️  consecutive failures are NOT independent — do not raise this rate to a "
          "power and call it the give-up risk (see ZERO_FRAME_RETRIES).")
    return 0


def _sei(src: str, seconds: int) -> int:
    """Does this camera emit SEI (NAL type 6)? The question the deleted -bsf:v was for.

    Uses FFmpeg's own bitstream parser, and a positive control that MUST show SEI —
    a zero from a detector never shown able to report non-zero means nothing.
    """
    def histogram(path: str) -> collections.Counter:
        pr = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "trace", "-i", path,
             "-c:v", "copy", "-bsf:v", "trace_headers", "-f", "null", "-"],
            capture_output=True, text=True, errors="replace")
        c: collections.Counter = collections.Counter()
        for line in pr.stderr.splitlines():
            if "trace_headers" in line and "nal_unit_type: " in line:
                tail = line.split("nal_unit_type: ", 1)[1]
                c[tail.split("(")[0].strip()] += 1
        return c

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cam, ctrl = f"{td}/cam.h264", f"{td}/ctrl.h264"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-rtsp_transport", "tcp", "-i", src, "-t", str(seconds),
                        "-an", "-c:v", "copy", "-f", "h264", cam, "-y"], check=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "testsrc=size=320x240:rate=5", "-t", "1",
                        "-c:v", "libx264", "-f", "h264", ctrl, "-y"], check=True)
        cam_h, ctrl_h = histogram(cam), histogram(ctrl)

    print(f"camera NAL histogram ({seconds}s): {dict(cam_h)}")
    print(f"control NAL histogram:            {dict(ctrl_h)}")
    if ctrl_h.get("6", 0) == 0:
        print("\nCONTROL FAILED: the detector did not report SEI on a stream that has "
              "it. The camera reading below is a NON-ANSWER.")
        return 1
    print(f"\ncontrol sees SEI: {ctrl_h['6']} => the detector can report non-zero")
    n = cam_h.get("6", 0)
    print(f"camera SEI (NAL type 6): {n}")
    print("=> this camera CANNOT answer the SEI question" if n == 0
          else "=> this camera DOES emit SEI; it can answer the question")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="rtsp:// URL of a live camera")
    ap.add_argument("--race-rate", type=int, metavar="N",
                    help="instead of the legs, run the shipping argv N times")
    ap.add_argument("--sei", action="store_true",
                    help="instead of the legs, count SEI NAL units on the camera")
    ap.add_argument("--sei-seconds", type=int, default=12)
    a = ap.parse_args()
    if not a.source.startswith("rtsp://"):
        print("--source must be rtsp:// (what _is_allowed_source permits)")
        return 2
    if a.sei:
        return _sei(a.source, a.sei_seconds)
    if a.race_rate:
        return asyncio.run(_race_rate(a.source, a.race_rate))
    return asyncio.run(_legs(a.source))


if __name__ == "__main__":
    sys.exit(main())
