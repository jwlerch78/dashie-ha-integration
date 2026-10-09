"""Device ids are TRUNCATED, never shown whole, anywhere in a diagnostics dump (2026-10-08).

The full id addresses the tablet's remote-command channel, and these dumps get attached to public
GitHub issues. Support still matches a dump to a device by the 6-character prefix.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.dashie import diagnostics as diag
from custom_components.dashie.const import CONF_DEVICE_ID, CONF_HOST, CONF_PASSWORD, DOMAIN

FULL = "8d60f4261b2e97ae7d1df0cb547cc9fb"   # 32-hex class
SHORT = "a1b2c3d4e5f60718"                   # 16-hex class
LOG = [
    "=== Dashie App ===",
    f"15:28:07.609 [INFO] WakeProbe: WAKEPROBE v1 ts=1 dev={FULL} engine=dual trig=1",
    "15:28:45.878 [INFO] MEMORY: WakeWord: engine=dual",
]


def _assert_no_full_id(obj, full):
    assert full not in repr(obj), f"full id leaked: {obj!r}"


def test_truncate_ids_masks_strings_nested_and_keeps_a_marked_prefix():
    out = diag._truncate_ids({"a": [FULL, {"b": f"x {FULL} y"}], "n": 3}, {FULL})
    assert out == {"a": ["8d60f4…", {"b": "x 8d60f4… y"}], "n": 3}


def test_truncate_ids_control_short_and_absent_ids_untouched():
    # A short string that happens to match must not be mangled, and text without an id is unchanged.
    assert diag._truncate_ids({"a": "dashie"}, {"dashie"}) == {"a": "dashie"}
    assert diag._truncate_ids(LOG[2], {FULL}) == LOG[2]


def test_device_id_is_not_in_to_redact():
    # HA's redactor would replace it wholesale with **REDACTED**, undoing the truncation.
    assert CONF_DEVICE_ID not in diag.TO_REDACT


async def test_config_entry_dump_truncates_id_in_config_AND_device_log(hass):
    entry = SimpleNamespace(entry_id="e1", data={CONF_DEVICE_ID: FULL, CONF_HOST: "192.168.1.5", CONF_PASSWORD: "pw"})
    hass.data[DOMAIN] = {"e1": object()}
    with patch.object(diag, "_fetch_device_log", AsyncMock(return_value=list(LOG))):
        out = await diag.async_get_config_entry_diagnostics(hass, entry)
    _assert_no_full_id(out, FULL)
    assert out["config_entry"][CONF_DEVICE_ID] == "8d60f4…"
    assert out["config_entry"][CONF_PASSWORD] == "**REDACTED**"
    assert "dev=8d60f4…" in out["device_log"][1]


async def test_device_dump_truncates_identifiers_and_log_for_the_16_hex_class(hass):
    entry = SimpleNamespace(entry_id="e1", data={CONF_DEVICE_ID: SHORT})
    device = SimpleNamespace(name="Kitchen", model="m", manufacturer="Dashie", sw_version="1",
                             identifiers={(DOMAIN, SHORT)})
    hass.data[DOMAIN] = {"e1": object()}
    log = [f"dev={SHORT}"]
    with patch.object(diag, "_fetch_device_log", AsyncMock(return_value=log)):
        out = await diag.async_get_device_diagnostics(hass, entry, device)
    _assert_no_full_id(out, SHORT)
    assert out["device"]["identifiers"] == [[DOMAIN, "a1b2c3…"]]
    assert out["device_log"] == ["dev=a1b2c3…"]
