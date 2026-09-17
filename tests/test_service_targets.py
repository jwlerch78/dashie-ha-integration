"""Services reach only the device they are aimed at.

Every service that takes `device_id` used to loop over ALL configured devices and ignore
the field, so `dashie.speak` aimed at the kitchen tablet spoke on every tablet in the house.

The UI fills `device_id` with an entity picker, so the value is normally an entity id (or a
list of them); YAML automations may pass a device id instead. Both must resolve to exactly
the devices they name. An omitted `device_id` still reaches every device, as before, so
existing automations that leave it out keep working. A value that matches no Dashie device
is an error, not a silent no-op.
"""
import re
from unittest.mock import AsyncMock, patch

import pytest
from aioresponses import aioresponses
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "dashie"
HOSTS = {"a": "192.168.23.10", "b": "192.168.23.11"}


def _speak_calls(mock: aioresponses, host: str) -> int:
    """How many textToSpeech requests reached this host."""
    return sum(
        len(calls)
        for (method, url), calls in mock.requests.items()
        if url.host == host and url.query.get("cmd") == "textToSpeech"
    )


@pytest.fixture
async def two_devices(hass: HomeAssistant):
    """Two loaded Dashie entries, each with one entity and one device."""
    hass.set_state(CoreState.running)
    entries, entities, devices = {}, {}, {}
    with aioresponses() as mock, patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
        AsyncMock(return_value=True),
    ):
        for key, host in HOSTS.items():
            mock.get(re.compile(rf"http://{re.escape(host)}:2323/.*"),
                     payload={"deviceID": f"dev-{key}", "deviceName": key}, repeat=True)
        for key, host in HOSTS.items():
            entry = MockConfigEntry(
                domain=DOMAIN, unique_id=f"dev-{key}", title=f"Tablet {key.upper()}",
                data={"host": host, "port": 2323, "device_id": f"dev-{key}"},
            )
            entry.add_to_hass(hass)
            await hass.config_entries.async_setup(entry.entry_id)
            entries[key] = entry
        await hass.async_block_till_done()

    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    for key, entry in entries.items():
        device = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"dev-{key}")}
        )
        entity = ent_reg.async_get_or_create(
            "sensor", DOMAIN, f"battery-{key}", config_entry=entry, device_id=device.id
        )
        entities[key], devices[key] = entity.entity_id, device.id
    return entities, devices


def _mock_both(mock: aioresponses) -> None:
    for host in HOSTS.values():
        mock.get(re.compile(rf"http://{re.escape(host)}:2323/.*"),
                 payload={"status": "OK", "message": "ok"}, repeat=True)


async def test_entity_target_reaches_only_that_device(hass, two_devices):
    entities, _ = two_devices
    with aioresponses() as mock:
        _mock_both(mock)
        await hass.services.async_call(
            DOMAIN, "speak", {"device_id": entities["a"], "message": "hi"}, blocking=True
        )
    assert _speak_calls(mock, HOSTS["a"]) == 1
    assert _speak_calls(mock, HOSTS["b"]) == 0, "a service aimed at A reached B"


async def test_device_id_target_reaches_only_that_device(hass, two_devices):
    _, devices = two_devices
    with aioresponses() as mock:
        _mock_both(mock)
        await hass.services.async_call(
            DOMAIN, "speak", {"device_id": devices["b"], "message": "hi"}, blocking=True
        )
    assert _speak_calls(mock, HOSTS["a"]) == 0, "a service aimed at B reached A"
    assert _speak_calls(mock, HOSTS["b"]) == 1


async def test_list_target_reaches_each_named_device_once(hass, two_devices):
    entities, devices = two_devices
    with aioresponses() as mock:
        _mock_both(mock)
        await hass.services.async_call(
            DOMAIN, "speak",
            {"device_id": [entities["a"], devices["a"], entities["b"]], "message": "hi"},
            blocking=True,
        )
    assert _speak_calls(mock, HOSTS["a"]) == 1
    assert _speak_calls(mock, HOSTS["b"]) == 1


async def test_omitted_target_still_reaches_every_device(hass, two_devices):
    with aioresponses() as mock:
        _mock_both(mock)
        await hass.services.async_call(DOMAIN, "speak", {"message": "hi"}, blocking=True)
    assert _speak_calls(mock, HOSTS["a"]) == 1
    assert _speak_calls(mock, HOSTS["b"]) == 1


async def test_unknown_target_is_an_error_not_a_broadcast(hass, two_devices):
    with aioresponses() as mock:
        _mock_both(mock)
        with pytest.raises(ServiceValidationError, match="sensor.not_a_tablet"):
            await hass.services.async_call(
                DOMAIN, "speak", {"device_id": "sensor.not_a_tablet", "message": "hi"},
                blocking=True,
            )
    assert _speak_calls(mock, HOSTS["a"]) == 0
    assert _speak_calls(mock, HOSTS["b"]) == 0
