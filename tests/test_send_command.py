"""A command the device refuses, or a device that does not answer, is an error the user sees.

Home Assistant's button writes its "last pressed" state before awaiting the press, and
switches, selects and services likewise return normally unless they raise, so a
`send_command` that only returned False made every refused or undelivered command look
like it worked. The only way to tell the frontend (and an automation) is to raise a
HomeAssistantError, which is also what `continue_on_error: true` lets a script step skip.

What these tests pin:
- The device's own reply shapes (a JSON body with status ERROR on 400/401, plain 200s,
  empty or non-JSON bodies, a 500 page, timeouts, refused connections) through the real
  response parsing, and the exact log lines the boolean path has always written, so
  log-based alerting does not change as a side effect.
- Entity actions raise with the device name and the reason.
- Services try every targeted device before raising once, naming each failure, and a
  script step marked continue_on_error carries on past that error.
- Background pushes (timers, voice-config refresh) never raise, and log one DROP line
  per device per change of state rather than one per second.
"""
import asyncio
import logging
import re

import aiohttp
import pytest
from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.dashie.coordinator import DashieCoordinator

from .test_service_targets import HOSTS, two_devices  # noqa: F401 (fixture)

HOST = "192.168.23.20"
URL = re.compile(rf"http://{re.escape(HOST)}:2323/.*")
LOGGER = "custom_components.dashie.coordinator"
REFUSAL = {"status": "ERROR", "message": "Reboot failed: requires root"}


@pytest.fixture
async def coordinator(hass: HomeAssistant):
    entry = MockConfigEntry(domain="dashie", title="Kitchen Tablet",
                            data={"host": HOST, "port": 2323})
    entry.add_to_hass(hass)
    coord = DashieCoordinator(hass, HOST, 2323, "", config_entry=entry)
    yield coord
    if coord._session and not coord._session.closed:
        await coord._session.close()


# (name, aioresponses kwargs, send_command result, exact log line or prefix)
SHAPES = [
    ("400 json refusal", {"status": 400, "payload": REFUSAL}, False,
     f"Command rebootDevice refused by {HOST}: Reboot failed: requires root"),
    ("401 json refusal", {"status": 401, "payload": {"status": "ERROR", "message": "Invalid password"}},
     False, f"Command rebootDevice refused by {HOST}: Invalid password"),
    ("200 ok", {"status": 200, "payload": {"status": "OK", "message": "done"}}, True,
     "Command rebootDevice sent successfully"),
    ("200 empty", {"status": 200, "body": ""}, True, "Command rebootDevice sent successfully"),
    ("200 text", {"status": 200, "body": "hello", "content_type": "text/plain"}, True,
     "Command rebootDevice sent successfully"),
    ("200 malformed", {"status": 200, "body": "{not json"}, True,
     "Command rebootDevice sent successfully"),
    ("500 html", {"status": 500, "body": "<html>oops</html>", "content_type": "text/html"}, False,
     "Connection error sending command rebootDevice: 500, message="),
    ("timeout", {"exception": asyncio.TimeoutError()}, False,
     f"Timeout sending command rebootDevice to {HOST}"),
    ("refused connection", {"exception": aiohttp.ClientConnectionError("Connection refused")}, False,
     "Connection error sending command rebootDevice: Connection refused"),
]


@pytest.mark.parametrize("name,response,expected,log", SHAPES, ids=[s[0] for s in SHAPES])
async def test_send_command_bool_path_and_its_log_lines_are_unchanged(
    coordinator, caplog, name, response, expected, log
):
    with aioresponses() as mock, caplog.at_level(logging.DEBUG, logger=LOGGER):
        mock.get(URL, **response)
        assert await coordinator.send_command("rebootDevice") is expected
    lines = [r.getMessage() for r in caplog.records if r.name == LOGGER and "rebootDevice" in r.getMessage()]
    assert lines, f"{name}: no log line"
    assert lines[-1].startswith(log), f"{name}: log changed: {lines[-1]!r}"


async def test_a_refusal_raises_with_the_device_and_its_message(coordinator):
    from custom_components.dashie.commands import DashieCommandError

    with aioresponses() as mock:
        mock.get(URL, status=400, payload=REFUSAL)
        with pytest.raises(DashieCommandError) as err:
            await coordinator.async_command("rebootDevice")
    assert isinstance(err.value, HomeAssistantError)
    assert str(err.value) == "Kitchen Tablet refused rebootDevice: Reboot failed: requires root"


async def test_an_offline_device_raises_naming_the_failure(coordinator):
    from custom_components.dashie.commands import DashieCommandError

    with aioresponses() as mock:
        mock.get(URL, exception=asyncio.TimeoutError())
        with pytest.raises(DashieCommandError) as err:
            await coordinator.async_command("rebootDevice")
    assert str(err.value) == "Kitchen Tablet did not respond to rebootDevice (TimeoutError)"


async def test_an_error_page_raises_as_a_refusal_with_its_status(coordinator):
    from custom_components.dashie.commands import DashieCommandError

    with aioresponses() as mock:
        mock.get(URL, status=500, body="<html>oops</html>", content_type="text/html")
        with pytest.raises(DashieCommandError, match=r"^Kitchen Tablet refused rebootDevice: HTTP 500"):
            await coordinator.async_command("rebootDevice")


async def test_a_successful_command_does_not_raise(coordinator):
    with aioresponses() as mock:
        mock.get(URL, status=200, payload={"status": "OK"})
        assert await coordinator.async_command("rebootDevice") is None


async def test_a_button_press_on_a_refusing_device_raises(coordinator):
    from custom_components.dashie.button import DashieRebootButton
    from custom_components.dashie.commands import DashieCommandError

    button = DashieRebootButton(coordinator, "dev-kitchen")
    with aioresponses() as mock:
        mock.get(URL, status=400, payload=REFUSAL)
        with pytest.raises(DashieCommandError, match="requires root"):
            await button.async_press()


def _speak_calls(mock, host):
    return sum(len(c) for (m, u), c in mock.requests.items()
               if u.host == host and u.query.get("cmd") == "textToSpeech")


async def test_a_service_reaches_every_device_before_raising_once(hass, two_devices):  # noqa: F811
    from custom_components.dashie.commands import DashieCommandError

    with aioresponses() as mock:
        mock.get(re.compile(rf"http://{re.escape(HOSTS['a'])}:2323/.*"),
                 status=400, payload={"status": "ERROR", "message": "TTS unavailable"}, repeat=True)
        mock.get(re.compile(rf"http://{re.escape(HOSTS['b'])}:2323/.*"),
                 exception=asyncio.TimeoutError(), repeat=True)
        with pytest.raises(DashieCommandError) as err:
            await hass.services.async_call("dashie", "speak", {"message": "hi"}, blocking=True)
    assert _speak_calls(mock, HOSTS["a"]) == 1
    assert _speak_calls(mock, HOSTS["b"]) == 1
    message = str(err.value)
    assert "Tablet A refused textToSpeech: TTS unavailable" in message
    assert "Tablet B did not respond to textToSpeech (TimeoutError)" in message


async def test_one_failing_device_does_not_stop_the_others(hass, two_devices):  # noqa: F811
    from custom_components.dashie.commands import DashieCommandError

    entities, _ = two_devices
    with aioresponses() as mock:
        mock.get(re.compile(rf"http://{re.escape(HOSTS['a'])}:2323/.*"),
                 exception=aiohttp.ClientConnectionError("Connection refused"), repeat=True)
        mock.get(re.compile(rf"http://{re.escape(HOSTS['b'])}:2323/.*"),
                 payload={"status": "OK"}, repeat=True)
        with pytest.raises(DashieCommandError) as err:
            await hass.services.async_call(
                "dashie", "speak", {"device_id": [entities["a"], entities["b"]], "message": "hi"},
                blocking=True,
            )
    assert _speak_calls(mock, HOSTS["b"]) == 1
    assert "Tablet B" not in str(err.value)


async def test_continue_on_error_skips_past_a_refused_command(hass, two_devices):  # noqa: F811
    from homeassistant.helpers import config_validation as cv
    from homeassistant.helpers.script import Script

    entities, _ = two_devices
    events = async_capture_events(hass, "after_dashie_step")
    script = Script(
        hass,
        cv.SCRIPT_SCHEMA([
            {"action": "dashie.speak", "continue_on_error": True,
             "data": {"device_id": entities["a"], "message": "hi"}},
            {"event": "after_dashie_step"},
        ]),
        "test", "dashie",
    )
    with aioresponses() as mock:
        mock.get(re.compile(rf"http://{re.escape(HOSTS['a'])}:2323/.*"),
                 status=400, payload={"status": "ERROR", "message": "nope"}, repeat=True)
        await script.async_run(context=None)
        await hass.async_block_till_done()
    assert len(events) == 1, "continue_on_error did not carry the script past the refusal"
    assert _speak_calls(mock, HOSTS["a"]) == 1


async def test_without_continue_on_error_the_script_stops_at_that_step(hass, two_devices):  # noqa: F811
    """Control for the test above: the same script must stop when the step is not marked."""
    from homeassistant.helpers import config_validation as cv
    from homeassistant.helpers.script import Script

    entities, _ = two_devices
    events = async_capture_events(hass, "after_dashie_step")
    script = Script(
        hass,
        cv.SCRIPT_SCHEMA([
            {"action": "dashie.speak", "data": {"device_id": entities["a"], "message": "hi"}},
            {"event": "after_dashie_step"},
        ]),
        "test", "dashie",
    )
    with aioresponses() as mock:
        mock.get(re.compile(rf"http://{re.escape(HOSTS['a'])}:2323/.*"),
                 status=400, payload={"status": "ERROR", "message": "nope"}, repeat=True)
        with pytest.raises(HomeAssistantError, match="Tablet A refused textToSpeech: nope"):
            await script.async_run(context=None)
        await hass.async_block_till_done()
    assert events == []


async def test_background_pushes_never_raise_and_log_once_per_change(coordinator, caplog):
    from custom_components.dashie.commands import async_push_to_all

    def drops():
        return [r.getMessage() for r in caplog.records if r.getMessage().startswith("DROP: timer push")]

    with caplog.at_level(logging.INFO):
        with aioresponses() as mock:
            mock.get(URL, exception=asyncio.TimeoutError(), repeat=True)
            for _ in range(3):
                await async_push_to_all([coordinator], "showTimer", what="timer")
        assert drops() == ["DROP: timer push to Kitchen Tablet failed: "
                           "Kitchen Tablet did not respond to showTimer (TimeoutError)"]

        with aioresponses() as mock:
            mock.get(URL, payload={"status": "OK"}, repeat=True)
            await async_push_to_all([coordinator], "showTimer", what="timer")
            await async_push_to_all([coordinator], "showTimer", what="timer")
        assert len(drops()) == 1

        with aioresponses() as mock:
            mock.get(URL, exception=asyncio.TimeoutError(), repeat=True)
            await async_push_to_all([coordinator], "showTimer", what="timer")
        assert len(drops()) == 2, "a new failure after recovery must be logged again"


async def test_failures_name_the_device_as_home_assistant_shows_it(hass: HomeAssistant):
    """Two tablets of the same model share a config entry title; the messages must not."""
    from homeassistant.helpers import device_registry as dr

    from custom_components.dashie.commands import device_label

    dev_reg = dr.async_get(hass)
    coords = []
    for n, (name, user_name) in enumerate([("Lerch Family Tablet", None), ("rk3576_u", "Mio 15")]):
        entry = MockConfigEntry(domain="dashie", title="rk3576_u",
                                data={"host": f"192.168.23.3{n}", "port": 2323})
        entry.add_to_hass(hass)
        device = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("dashie", f"dev-{n}")}, name=name
        )
        if user_name:
            dev_reg.async_update_device(device.id, name_by_user=user_name)
        coords.append(DashieCoordinator(hass, f"192.168.23.3{n}", 2323, "", config_entry=entry))

    assert device_label(coords[0]) == "Lerch Family Tablet"
    assert device_label(coords[1]) == "Mio 15"  # the name the user gave wins


async def test_label_falls_back_to_the_entry_title_then_the_host(hass: HomeAssistant, coordinator):
    from custom_components.dashie.commands import device_label

    assert device_label(coordinator) == "Kitchen Tablet"  # no device registered yet
    bare = DashieCoordinator(hass, "192.168.23.99", 2323, "")
    assert device_label(bare) == "192.168.23.99"
