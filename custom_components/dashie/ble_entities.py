"""Bluetooth for HA: the per-tablet "Bluetooth devices" sensor.

This entity EXPOSES what ``ble_webhook`` already derived; it computes nothing. On every
5-second batch ``DashieBle._heard_via`` asks HA's Bluetooth manager, once per address,
which receiver HA is currently using for that device, and records
``{name, address, via, rssi}`` as it goes. This reads that list.

🔴 **The state IS the count.** Nothing downstream may re-derive it from ``devices`` — a
second derivation is a second source of truth, and the two would disagree the moment one
of them was changed. The console badge reads the state.

``via`` is HA's view, not the tablet's: ``"this"`` means HA is using THIS tablet as the
best receiver for that device, ``"other"`` means HA hears it better somewhere else. A
tablet cannot know that on its own, which is why HA is the one that says it.

⚠️ The count moves on its own. Which receiver is "best" follows RSSI, so two tablets can
trade a device between them with nothing changing but the radio. The invariant worth
testing is that every device HA hears is claimed by exactly one tablet — not that any
particular tablet reads any particular number at any particular moment.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .ble_webhook import ble_devices_signal
from .const import DOMAIN
from .coordinator import DashieCoordinator
from .entity import DashieEntity


class DashieBluetoothDevicesSensor(DashieEntity, SensorEntity):
    """How many of HA's Bluetooth devices this tablet is HA's best receiver for."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "bluetooth_devices"
    _attr_icon = "mdi:bluetooth-audio"

    def __init__(self, coordinator: DashieCoordinator, device_id: str, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{device_id}_bluetooth_devices"
        self._attr_name = "Bluetooth devices"

    @property
    def _ble(self) -> Any | None:
        """The entry's ``DashieBle``, looked up late.

        The sensor platform is forwarded (``__init__.py:173``) before ``async_setup_ble``
        runs (``:338``), so a reference captured in ``__init__`` would be ``None`` forever.
        """
        return self.hass.data.get(DOMAIN, {}).get(f"{self._entry_id}_ble")

    async def async_added_to_hass(self) -> None:
        """Write state when a batch updates the view, rather than polling for it."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, ble_devices_signal(self._entry_id), self._async_on_update
            )
        )

    @callback
    def _async_on_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Unavailable when this tablet is not a Bluetooth receiver for HA.

        That is the ``enabled: false`` path: the tablet has turned Bluetooth for HA off,
        the scanner is unregistered, and there is no view to report — distinct from a
        tablet that is scanning and genuinely hears nothing, which is 0.
        """
        ble = self._ble
        return super().available and ble is not None and ble.is_scanning

    @property
    def native_value(self) -> int | None:
        """The number of devices HA is using THIS tablet for."""
        ble = self._ble
        if ble is None:
            return None
        return sum(1 for device in ble.ble_devices if device["via"] == "this")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Every device this tablet hears, including the ones HA prefers elsewhere."""
        ble = self._ble
        return {"devices": list(ble.ble_devices) if ble is not None else []}
