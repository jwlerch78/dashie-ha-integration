"""D-281 items 4 and 6: Device Owner status, and the app's package name.

Both read fields the device ALREADY sends, so there is no Android half:
  isDeviceOwner  — DeviceInfoHandler.kt:102, beside isDeviceAdmin
  foregroundApp  — DeviceInfoHandler.kt:139, set to context.packageName

⚠️ The `foregroundApp` name is a defect, not a description: the device sets it to
its own package unconditionally, so it never names the foreground app. It is
surfaced as `package_name`, and the test below pins that so nobody "fixes" the
entity to match the wire name and enshrines the wrong meaning.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest
from homeassistant.helpers.entity import EntityCategory

from custom_components.dashie.binary_sensor import (
    DashieDeviceAdminSensor,
    DashieDeviceOwnerSensor,
    async_setup_entry as binary_sensor_setup,
)
from custom_components.dashie.const import CONF_DEVICE_ID, DOMAIN
from custom_components.dashie.sensor import (
    DashiePackageNameSensor,
    async_setup_entry as sensor_setup,
)


def _entity(cls, data):
    coordinator = Mock()
    coordinator.data = data
    coordinator.host = "1.2.3.4"
    coordinator.port = 2323
    return cls(coordinator, "dev1")


# --- item 4: Device Owner -----------------------------------------------------

@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"isDeviceOwner": True}, True),
        ({"isDeviceOwner": False}, False),
        # Field absent from a NON-empty payload: False, the same default its Device
        # Admin sibling uses — an old device that does not report the flag is not a
        # Device Owner as far as anything here can tell.
        ({"isDeviceAdmin": True}, False),
        # No data at all: UNKNOWN, not False. An empty dict is falsy, so this is the
        # "coordinator has not polled yet" case and must not read as "not owner".
        ({}, None),
        (None, None),
    ],
)
def test_device_owner_state(payload, expected) -> None:
    assert _entity(DashieDeviceOwnerSensor, payload).is_on is expected


def test_device_owner_is_not_reading_device_admin() -> None:
    """The two states are INDEPENDENT — Device Owner is strictly stronger, and a
    device can have either without the other. Reading the wrong key would look
    plausible on most devices and be wrong on exactly the ones that matter."""
    data = {"isDeviceOwner": True, "isDeviceAdmin": False}
    assert _entity(DashieDeviceOwnerSensor, data).is_on is True
    assert _entity(DashieDeviceAdminSensor, data).is_on is False
    flipped = {"isDeviceOwner": False, "isDeviceAdmin": True}
    assert _entity(DashieDeviceOwnerSensor, flipped).is_on is False
    assert _entity(DashieDeviceAdminSensor, flipped).is_on is True


# --- item 6: package name -----------------------------------------------------

@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"foregroundApp": "com.dashieapp.Dashie"}, "com.dashieapp.Dashie"),
        ({"foregroundApp": "com.dashieapp.Dashie.staging"}, "com.dashieapp.Dashie.staging"),
        ({"appVersionName": "1.1.1"}, None),   # non-empty payload, field absent
        ({}, None),                            # not polled yet
        (None, None),
    ],
)
def test_package_name_state(payload, expected) -> None:
    assert _entity(DashiePackageNameSensor, payload).native_value == expected


def test_package_name_is_not_hardcoded() -> None:
    """It must report what the DEVICE says. Hardcoding the prod package would make
    every flavor look like prod, which is the exact confusion this entity exists to
    resolve (three builds answered to '1.1.1' on one desk, 2026-09-09)."""
    assert _entity(
        DashiePackageNameSensor, {"foregroundApp": "com.example.other"}
    ).native_value == "com.example.other"


def test_package_name_is_not_exposed_as_foreground_app() -> None:
    """Pins the naming decision against the misleading wire field name."""
    ent = _entity(DashiePackageNameSensor, {})
    assert "foreground" not in (ent.unique_id or "").lower()
    assert "foreground" not in (ent.name or "").lower()
    assert ent.unique_id == "dev1_package_name"


# --- both: category, enabled-by-default, and actually REACHED -----------------

@pytest.mark.parametrize("cls", [DashieDeviceOwnerSensor, DashiePackageNameSensor])
def test_both_are_diagnostic_and_enabled_by_default(cls) -> None:
    """John asked for these enabled by default (2026-10-16). HA enables an entity
    unless it opts out, so this leg fails if anyone adds
    `_attr_entity_registry_enabled_default = False` later."""
    ent = _entity(cls, {})
    assert ent.entity_category is EntityCategory.DIAGNOSTIC
    assert ent.entity_registry_enabled_default is True


@pytest.mark.asyncio
async def test_both_entities_are_actually_registered_by_their_platform() -> None:
    """🔴 A class that exists but is missing from its platform's entities list is
    authored-but-unreached: every unit test above would still pass and the entity
    would never appear in HA. This drives the real async_setup_entry."""
    coordinator = Mock()
    coordinator.data = {}
    entry = Mock()
    entry.entry_id = "e1"
    entry.data = {CONF_DEVICE_ID: "dev1"}
    hass = Mock()
    hass.data = {DOMAIN: {"e1": coordinator}}

    added: list = []
    for setup in (binary_sensor_setup, sensor_setup):
        await setup(hass, entry, lambda ents, *a, **k: added.extend(ents))

    types = {type(e) for e in added}
    assert DashieDeviceOwnerSensor in types, "Device Owner never reaches HA"
    assert DashiePackageNameSensor in types, "Package Name never reaches HA"
