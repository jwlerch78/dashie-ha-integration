"""`_redact_url` is the only thing between a camera password and the log.

It is used by stream_proxy (FFmpeg cmd debug, stderr tails, DROP: lines),
stream_multiplexer and stream_resolve — six call sites, and until 2026-10-16 it
had **no tests at all**. The gap that prompted these: it handled
`rtsp://user:pass@host` but not `rtsp://host?user=x&password=y`, a shape Reolink
and Dahua accept and users paste into generic-camera. On that shape the password
went to the log in plaintext.
"""
from __future__ import annotations

import pytest

from custom_components.dashie.stream_proxy import _redact_url

SECRET = "S3cretPw"


@pytest.mark.parametrize(
    "url",
    [
        # The documented Reolink / Tapo form.
        f"rtsp://admin:{SECRET}@192.168.1.50:554/h264Preview_01_main",
        # The query form — this is the leg that FAILS on the pre-2026-10-16
        # implementation, which only ever matched `://...@`.
        f"rtsp://192.168.1.50:554/h264Preview_01_main?user=admin&password={SECRET}",
        f"rtsp://192.168.1.50:554/cam?username=admin&pwd={SECRET}",
        f"rtsp://192.168.1.50:554/cam?token={SECRET}",
        f"rtsp://192.168.1.50:554/cam?auth={SECRET}",
        # Embedded in a log line, which is how stderr tails actually arrive.
        f"[in#0/rtsp @ 0x1] rtsp://admin:{SECRET}@192.168.1.50:554/x: Connection refused",
        # go2rtc / snapshot URLs are http(s) and reach the same redactor.
        f"http://192.168.1.50:1984/api/frame.jpeg?src=cam&password={SECRET}",
        f"https://admin:{SECRET}@example.invalid/snapshot",
    ],
)
def test_the_secret_never_survives(url: str) -> None:
    assert SECRET not in _redact_url(url)


def test_the_host_survives_so_the_log_is_still_diagnosable() -> None:
    """Over-redaction is safe but useless — the host is why you read the line."""
    out = _redact_url(f"rtsp://admin:{SECRET}@192.168.1.50:554/h264Preview_01_main")
    assert "192.168.1.50:554" in out
    assert "h264Preview_01_main" in out


def test_an_at_sign_in_the_path_does_not_eat_the_host() -> None:
    """The old pattern ran across slashes, so a path @ redacted the host as well."""
    out = _redact_url("rtsp://192.168.1.50:554/path@weird/stream")
    assert "192.168.1.50:554" in out
    assert "****" not in out


def test_a_url_with_no_credentials_is_returned_unchanged() -> None:
    url = "rtsp://192.168.86.147:8554/"
    assert _redact_url(url) == url


def test_non_credential_query_params_are_preserved() -> None:
    """Redacting fps/width would make the FFmpeg cmd debug line unreadable."""
    out = _redact_url(f"rtsp://host:554/cam?token={SECRET}&fps=5&width=640")
    assert "fps=5" in out and "width=640" in out
    assert SECRET not in out


def test_the_whole_ffmpeg_argv_can_be_redacted_elementwise() -> None:
    """How stream_proxy and stream_multiplexer actually call it."""
    argv = ["ffmpeg", "-i", f"rtsp://admin:{SECRET}@host:554/c", "-f", "mjpeg"]
    assert SECRET not in " ".join(_redact_url(a) for a in argv)
