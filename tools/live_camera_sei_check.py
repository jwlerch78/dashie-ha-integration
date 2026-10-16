#!/usr/bin/env python3
"""Does this camera emit SEI (NAL type 6)?

This is the question the `-bsf:v filter_units=remove_types=6` in the MJPEG proxy
was *meant* to answer and never did: placed after `-i` it bound to the MJPEG
output, which filter_units does not support, so FFmpeg refused the whole command
and the filter never ran once between v1.4.0 (2026-04-07) and 2026-10-14.

So this tool does not test the proxy — it characterizes a camera, to find a
source that can say whether stripping SEI was ever worth doing.

    python3 tools/live_camera_sei_check.py --source-file <chmod-600 file>

🔴 It always runs a POSITIVE CONTROL first: an x264-encoded clip that definitely
contains SEI. A zero from a detector never shown able to report non-zero is a
non-answer, and the first attempt at this control silently produced no file at
all — which would have made the camera's zero look like an answer.

Measured 2026-10-16: John's Samsung tablet RTSP server emits ZERO SEI (0 of 219
NAL units over 12 s / 3.5 MB), so it cannot answer the question.
"""
from __future__ import annotations

import argparse
import collections
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.dashie.stream_proxy import _redact_url as _R  # noqa: E402


def check(src: str, seconds: int) -> int:
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

    with tempfile.TemporaryDirectory() as td:
        cam, ctrl = f"{td}/cam.h264", f"{td}/ctrl.h264"
        # check=False + redaction: a CalledProcessError would print the whole
        # argv, and for a Reolink the URL *is* the credential.
        grab = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
             "-i", src, "-t", str(seconds), "-an", "-c:v", "copy", "-f", "h264",
             cam, "-y"],
            capture_output=True, text=True, errors="replace", check=False)
        if grab.returncode != 0:
            print(f"could not pull from the camera: {_R(grab.stderr)[-400:]}")
            return 1
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
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--source-file", metavar="PATH",
                     help="file whose first line is the rtsp:// URL. USE THIS for any "
                          "camera with credentials in the URL (Reolink, Dahua): argv is "
                          "world-readable via ps, a file can be chmod 600")
    src.add_argument("--source", help="rtsp:// URL, for a camera with NO credentials")
    ap.add_argument("--seconds", type=int, default=12)
    a = ap.parse_args()
    if a.source_file:
        pth = Path(a.source_file)
        mode = pth.stat().st_mode & 0o777
        if mode & 0o077:
            print(f"refusing: {a.source_file} is mode {mode:o}, readable by others.\n"
                  f"  chmod 600 {a.source_file}")
            return 2
        a.source = pth.read_text().strip().splitlines()[0].strip()
    else:
        print("note: --source puts the URL in argv, which `ps` shows to any local "
              "process. Use --source-file for a camera whose URL carries credentials.")
    if not a.source.startswith("rtsp://"):
        print("source must be rtsp://")
        return 2
    print(f"source: {_R(a.source)}")
    return check(a.source, a.seconds)


if __name__ == "__main__":
    sys.exit(main())
