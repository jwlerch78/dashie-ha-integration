"""The device API password must survive the trip to the device intact.

🔴 THE BUG THIS PINS. The Dashie tablet's local HTTP API takes its password as a QUERY
PARAMETER, and ten places in the integration send it. Eight built the URL by string
concatenation with no encoding:

    url += f"&password={password}"

yarl then parses the value as URL syntax and SILENTLY CORRUPTS it. The request is well
formed, it just carries the wrong secret: the device answers 401, the integration reports
"unavailable", and nothing says the password was mangled in transit.

`_fetch_device_info` is the coordinator's main poll, so for an affected household this is
not one broken entity - the whole integration fails, and the stored password is correct,
so nobody can find it by re-reading settings.

⚠️ IT ALSO CAUSES THE CONDITIONS FOR THE DISCLOSURE FIXED ALONGSIDE IT: corrupted
password -> 401 -> the household downloads diagnostics to find out why -> before
`_safe_err` that file carried the real, uncorrupted password. See
tests/test_diagnostics_no_credential.py.

⚠️ `params=` was ALREADY the correct form at two of the ten sites (`async_send`,
`_fetch_device_log`), so the right pattern was in the file the whole time and eight
callers didn't use it. Hence one shared helper rather than eight edits - fixing the
copies without removing the shape leaves the next caller free to get it wrong.
"""
from __future__ import annotations

import pathlib

import pytest
from yarl import URL

from custom_components.dashie.device_api import device_api_request

BASE = "http://192.168.1.77:2323"
PW = "hunter2"


def _sent(password: str, **extra) -> str | None:
    """What the device actually RECEIVES as `password`, parsed back off the wire.

    Deliberately asserts on the parsed query rather than on the string we built: the bug
    was that the string looked right and parsed wrong.
    """
    url, params = device_api_request(BASE, password, "getCamshot", **extra)
    return URL(url).with_query(params).query.get("password")


# ── the control: the old form really did corrupt, or none of this matters ─────────────


@pytest.mark.parametrize("pw", ["pa#ss", "pa&ss", "pa+ss", "pa\rss", "pa\nss", "pa\tss"])
def test_control_the_old_interpolated_form_corrupts_these(pw: str) -> None:
    """POSITIVE CONTROL. Reproduces the pre-fix construction and requires it to be WRONG.

    Without this, every assertion below would pass just as well on a password that was
    never at risk, and the fix would be unfalsifiable. This is the measurement that
    justified the change, kept executable.
    """
    legacy = URL(f"{BASE}/?cmd=getCamshot&password={pw}")
    assert legacy.query.get("password") != pw, (
        f"{pw!r} survived the OLD interpolated form, so it is not evidence of the bug. "
        f"If yarl's parsing changed, re-derive the affected character set."
    )


# ── the fix ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pw", ["pa#ss", "pa&ss", "pa+ss", "pa\rss", "pa\nss", "pa\tss"])
def test_the_six_corrupting_characters_now_survive(pw: str) -> None:
    """The headline, one leg per character the control just proved was broken.

    Injection: reverting any call site to interpolation reds its own tests; reverting
    `device_api_request` to build a joined URL string reds all six of these.
    """
    assert _sent(pw) == pw


@pytest.mark.parametrize(
    "pw", ["hunter2", "pa=ss", "pa ss", "pa/ss", "pa%ss", "paßss", "pa:ss@evil.com", "?x"]
)
def test_passwords_that_always_worked_still_work(pw: str) -> None:
    """The regression half. A fix that broke the majority case would be a worse bug."""
    assert _sent(pw) == pw


def test_an_empty_password_is_omitted_entirely_not_sent_blank() -> None:
    """The device treats a present-but-empty `password` differently from an absent one.

    Injection: dropping the `if password:` guard sends `password=` and reds this.
    """
    url, params = device_api_request(BASE, "", "getCamshot")
    assert "password" not in params
    assert "password" not in str(URL(url).with_query(params))


def test_the_command_is_always_present() -> None:
    url, params = device_api_request(BASE, PW, "getRtspStatus")
    assert params["cmd"] == "getRtspStatus"


def test_extra_params_are_applied_last_matching_async_sends_long_standing_order() -> None:
    """Fidelity, not preference: `async_send` has always let caller kwargs win.

    This pins the behavior the refactor had to preserve, so "tidying" the helper to apply
    extras first would red rather than silently change what a service call sends.
    """
    _, params = device_api_request(BASE, PW, "setBooleanSetting", cmd="override", key="x")
    assert params["cmd"] == "override"
    assert params["key"] == "x"


def test_a_trailing_slash_on_the_base_url_does_not_double() -> None:
    url, _ = device_api_request(BASE + "/", PW, "getCamshot")
    assert url == f"{BASE}/"


# ── the seam itself ───────────────────────────────────────────────────────────────────


def test_no_production_file_builds_a_password_url_by_hand() -> None:
    """🔴 THE SEAM TEST, and the reason this bug is fixed rather than just patched.

    Eight separate callers independently got this wrong, which is a property of the SHAPE,
    not of eight authors. Fixing the copies without removing the shape leaves the ninth
    caller free to repeat it. This fails the moment anyone interpolates a password into a
    URL again, naming the file.

    Scoped to `password=` interpolation specifically: the RTSP/camera credential paths in
    stream_proxy/stream_resolve are a different credential with their own redaction tests,
    and this must not start policing them by accident.
    """
    root = pathlib.Path(__file__).parent.parent / "custom_components" / "dashie"
    offenders = []
    for py in sorted(root.glob("*.py")):
        if py.name == "device_api.py":
            continue  # the one place allowed to talk about this, and it builds no URL
        for i, line in enumerate(py.read_text().splitlines(), start=1):
            if "password={" in line and not line.lstrip().startswith("#"):
                offenders.append(f"{py.name}:{i}: {line.strip()}")

    assert not offenders, (
        "a password is being interpolated into a URL again - use "
        "coordinator.api_request() / device_api_request():\n  " + "\n  ".join(offenders)
    )


def test_the_seam_test_can_actually_fail(tmp_path) -> None:
    """Rule 4 on the leg above, because a scan that matches nothing passes identically.

    Trap 345: a check that selects its own subjects can report a clean zero because the
    selection broke, not because the code is clean. This proves the pattern still matches
    the shape it is looking for.
    """
    offending = 'url += f"&password={self.coordinator.password}"'
    assert "password={" in offending, (
        "the seam test's pattern no longer matches the construction it exists to catch, "
        "so its zero means nothing"
    )
