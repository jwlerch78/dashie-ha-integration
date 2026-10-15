"""The MJPEG proxy's FFmpeg argv, and what the reconnect loop does when it fails.

Two defects are pinned here, found 2026-10-14 against released v1.6.2.

**The argv.** `-bsf:v filter_units=remove_types=6` sat immediately after `-i` from
v1.4.0 (2026-04-07) until 2026-10-14. FFmpeg reads options positionally: after `-i`
it is an *output* option, so it bound to the MJPEG encoder rather than the H.264
input its own comment named. `filter_units` has never supported `mjpeg`, so FFmpeg
refused the entire command — measured exit 234 on 6.1.6, 7.1.5, 8.1.3 and 9.0.2,
each against a control run that passed on that same binary and source. It was a
no-op wherever it was tolerated and fatal wherever it was not; no household ever
got the SEI stripping it advertised.

⚠️ The tempting fix — moving it before `-i` — is wrong twice over. It produces
*byte-identical* output to deleting it (sha256-compared, on a source carrying SEI),
and FFmpeg 6 rejects an input-side `-bsf:v` outright. Deleting is the only arm that
passes on all four versions. Do not reintroduce it in either position.

**The silence.** A rejected argument is deterministic, but the loop retried it 10
times at `_LOGGER.debug`, having already sent the client `200 OK` and headers. So
the symptom was a camera that never loaded with *nothing in the log at default
level* — strictly harder to diagnose than a crash. The cause was that
`_pipe_frames_to_response` returned a bare bool: "client left" and "FFmpeg never
produced a frame" arrived as the same `False`. `PipeResult.frames` is that missing
distinction, and the two loop legs below are what make it load-bearing.

The pair L4/L5 is the point: L4 alone would pass if reconnect were disabled
entirely, so L5 (a mid-stream death still retries) is what gives L4 meaning.
"""
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import web

from custom_components.dashie import stream_proxy
from custom_components.dashie.stream_proxy import (
    PipeResult,
    _build_ffmpeg_cmd,
    DashieMjpegStreamView,
)

SOURCE = "rtsp://cam.invalid/stream1"


def _argv(**kw):
    """The software-path argv, as the view builds it."""
    return _build_ffmpeg_cmd(
        kw.get("source", SOURCE),
        kw.get("fps", 15),
        kw.get("quality", 5),
        kw.get("width"),
        kw.get("hw_accel", "software"),
    )


# ---------------------------------------------------------------- the argv

def test_argv_carries_no_bitstream_filter():
    """L1 — the pin. No `-bsf` in any position, for any hw_accel path."""
    for hw in ("software", "vaapi", "cuda", "v4l2m2m"):
        argv = _argv(hw_accel=hw)
        offenders = [a for a in argv if str(a).startswith("-bsf")]
        assert offenders == [], f"hw_accel={hw} reintroduced {offenders}"
        assert not any("filter_units" in str(a) for a in argv), hw


def test_argv_is_still_a_real_command():
    """L2 — positive control. L1 must not be satisfiable by an empty argv.

    Without this, deleting the body of _build_ffmpeg_cmd would turn L1 green.
    """
    argv = _argv()
    assert argv[0] == "ffmpeg"
    for expected in ("-i", SOURCE, "-c:v", "mjpeg", "-f", "-err_detect", "ignore_err"):
        assert expected in argv, f"{expected!r} missing — argv is not a real command"


def test_input_options_precede_the_input():
    """L3 — the ordering invariant the defect violated.

    `-err_detect ignore_err` is the surviving SEI mitigation and is an INPUT
    option, so it must sit before `-i`. This is the property that was wrong.
    """
    argv = _argv()
    assert argv.count("-i") == 1
    i_at = argv.index("-i")
    assert argv.index("-err_detect") < i_at
    assert argv.index("-analyzeduration") < i_at
    # and the output codec is after it
    assert argv.index("-c:v") > i_at


# -------------------------------------------------- the loop's two outcomes

class _FakeProc:
    def __init__(self, returncode=234):
        self.returncode = returncode
        self.stdout = None

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


async def _drive_loop(monkeypatch, pipe_results):
    """Run the view's reconnect loop, returning (spawn_count, log_records).

    `source=` is used deliberately: it bypasses the Frigate branch and entity
    resolution, so the loop under test is reached without standing up a camera.
    """
    monkeypatch.setattr(stream_proxy, "RECONNECT_DELAY", 0)
    spawns = []

    async def fake_exec(*cmd, **kw):
        spawns.append(cmd)
        return _FakeProc()

    results = list(pipe_results)

    async def fake_pipe(process, response, fps, entity_id):
        return results.pop(0) if results else PipeResult(True, 0)

    async def fake_prepare(self, request):
        self._payload_writer = Mock(transport=None)
        return None

    request = Mock()
    request.app = {"hass": Mock()}
    request.query = {"source": SOURCE}

    with patch.object(web.StreamResponse, "prepare", fake_prepare), \
         patch.object(stream_proxy.asyncio, "create_subprocess_exec", fake_exec), \
         patch.object(stream_proxy, "_pipe_frames_to_response", fake_pipe), \
         patch.object(stream_proxy, "_detect_hw_accel", AsyncMock(return_value="software")):
        await DashieMjpegStreamView().get(request, "camera.front")

    return spawns


@pytest.mark.asyncio
async def test_zero_frames_is_loud_and_does_not_retry(monkeypatch, caplog):
    """L4 — a run that delivered nothing must log ERROR with DROP: and stop."""
    caplog.set_level("DEBUG", logger=stream_proxy._LOGGER.name)
    spawns = await _drive_loop(monkeypatch, [PipeResult(False, 0)])

    assert len(spawns) == 1, f"retried a deterministic failure {len(spawns)} times"
    errors = [r for r in caplog.records if r.levelname == "ERROR" and "DROP:" in r.getMessage()]
    assert len(errors) == 1, [r.getMessage() for r in caplog.records]
    assert "no frames" in errors[0].getMessage()


@pytest.mark.asyncio
async def test_mid_stream_death_still_reconnects(monkeypatch, caplog):
    """L5 — the control that gives L4 meaning.

    If L4 were satisfied by disabling reconnect, this goes red: a stream that
    delivered frames and then stopped is transient and must be retried.
    """
    caplog.set_level("DEBUG", logger=stream_proxy._LOGGER.name)
    spawns = await _drive_loop(
        monkeypatch,
        [PipeResult(False, 12), PipeResult(False, 8), PipeResult(True, 3)],
    )

    assert len(spawns) == 3, "a mid-stream death must reconnect"
    assert not [r for r in caplog.records if r.levelname == "ERROR"], "not an error path"
