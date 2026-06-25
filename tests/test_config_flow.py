"""Config-flow tests for the Dashie integration.

These reproduce the real field failures we hit in June 2026:

* A dual-stack device (Mat's Echo Show 5) advertises IPv6 first over mDNS; the
  flow must pick the IPv4 address and reach setup, not silently abort.
* A user pasting ``http://<ip>/`` into the manual form must still connect.
* Re-adding an already-configured device must abort ``already_configured`` —
  NOT be mislabeled ``cannot_connect``.

``_fetch_device_info`` creates its own ``aiohttp.ClientSession``, so we mock at
the aiohttp layer with ``aioresponses`` (HA's ``aioclient_mock`` only covers the
shared session).
"""
from ipaddress import ip_address
from unittest.mock import patch

from aioresponses import aioresponses
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

try:  # HA 2025.2+ canonical location (John's prod is 2026.5)
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # older HA (e.g. 2025.1 in the local test harness)
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "dashie"
DEVICE_ID = "a83e167a70e648255f71a1744d25f740"
IPV4 = "192.168.23.96"
DEVICE_URL = f"http://{IPV4}:2323/?cmd=deviceInfo&type=json"

DEVICE_INFO_JSON = {
    "deviceID": DEVICE_ID,
    "stableDeviceID": DEVICE_ID,
    "deviceName": "Echo Show 5 (2nd Generation)",
}


def _mat_zeroconf() -> ZeroconfServiceInfo:
    """Mat's device: IPv6 (ULA) surfaced first via .host, IPv4 last in the list."""
    return ZeroconfServiceInfo(
        ip_address=ip_address("fc00:8b0:3de:468:d159:1b87:aec2:1e0c"),
        ip_addresses=[
            ip_address("fc00:8b0:3de:468:d159:1b87:aec2:1e0c"),
            ip_address("fc00:8b0:3de:468:4b48:166c:8dae:f514"),
            ip_address(IPV4),
        ],
        port=2323,
        hostname="echo-show-5.local.",
        type="_dashie-kiosk._tcp.local.",
        name="Echo Show 5 (2nd Generation)._dashie-kiosk._tcp.local.",
        properties={
            "name": "Echo Show 5 (2nd Generation)",
            "uuid": "8bf5b754-7ba8-4115-810e-bf3a3e45299a",
            "api_port": "2323",
        },
    )


async def test_zeroconf_prefers_ipv4_over_ipv6(hass: HomeAssistant) -> None:
    """Dual-stack discovery must hit the IPv4 deviceInfo URL and reach confirm.

    Only the IPv4 URL is mocked — if the flow regressed to using the IPv6
    ``.host``, the request would have no match and the flow would abort
    ``cannot_connect`` (the exact bug this guards against).
    """
    with aioresponses() as mock:
        mock.get(DEVICE_URL, payload=DEVICE_INFO_JSON)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_mat_zeroconf(),
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    # Prove the IPv4 address (not an IPv6) was the one contacted.
    assert any(IPV4 in str(url) for (_method, url) in mock.requests)

    # Finish the flow (stub entry setup so it doesn't touch a real socket) so no
    # discovery flow lingers into teardown, and confirm the IPv4 host is stored.
    with patch("custom_components.dashie.async_setup_entry", return_value=True):
        done = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert done["data"]["host"] == IPV4


async def test_user_flow_normalizes_http_scheme(hass: HomeAssistant) -> None:
    """A pasted ``http://<ip>/`` host is normalized and still connects."""
    init = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with aioresponses() as mock:
        mock.get(DEVICE_URL, payload=DEVICE_INFO_JSON)
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"], {"host": f"http://{IPV4}/", "port": 2323}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == IPV4  # stored clean, no scheme/slash


async def test_user_flow_already_configured_not_cannot_connect(
    hass: HomeAssistant,
) -> None:
    """Re-adding an existing device aborts already_configured, not cannot_connect."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={"host": IPV4, "port": 2323, "device_id": DEVICE_ID},
    ).add_to_hass(hass)

    init = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with aioresponses() as mock:
        mock.get(DEVICE_URL, payload=DEVICE_INFO_JSON)
        result = await hass.config_entries.flow.async_configure(
            init["flow_id"], {"host": IPV4, "port": 2323}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
