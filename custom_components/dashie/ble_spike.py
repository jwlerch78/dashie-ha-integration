"""SPIKE ONLY (Smart Home P9 connections, 10-04): a service that makes HA connect to a
Bluetooth device and read one characteristic, the way an integration would.

``dashie.ble_spike_read`` {address, characteristic} → {value_hex, value_text, source, ms}.
It goes through HA's own Bluetooth stack (bleak_retry_connector.establish_connection), so
HA picks the connection path itself; ``source`` says which receiver it chose. No HA
integration of the user's is needed to test a connection through a tablet.

Remove this module (and its call in ble_webhook.async_setup_ble) when the spike ends.
"""
from __future__ import annotations

import logging
import time

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE = "ble_spike_read"
SCHEMA = vol.Schema({vol.Required("address"): cv.string, vol.Required("characteristic"): cv.string})


def async_setup_spike_service(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE):
        return

    async def _read(call: ServiceCall) -> ServiceResponse:
        from bleak import BleakClient
        from bleak_retry_connector import establish_connection
        from homeassistant.components import bluetooth

        address = call.data["address"].upper()
        device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
        if device is None:
            raise HomeAssistantError(f"No connectable receiver hears {address}")
        source = (device.details or {}).get("source")
        started = time.monotonic()
        client = await establish_connection(BleakClient, device, address, max_attempts=1)
        try:
            value = await client.read_gatt_char(call.data["characteristic"])
        finally:
            await client.disconnect()
        ms = int((time.monotonic() - started) * 1000)
        text = bytes(value).decode("utf-8", errors="replace")
        _LOGGER.info("SMARTHOME_BLE_SPIKE_READ %s via %s in %d ms: %s", address, source, ms, bytes(value).hex())
        return {"value_hex": bytes(value).hex(), "value_text": text, "source": source, "ms": ms}

    hass.services.async_register(DOMAIN, SERVICE, _read, schema=SCHEMA, supports_response=SupportsResponse.ONLY)
