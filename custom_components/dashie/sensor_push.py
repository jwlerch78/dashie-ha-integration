"""Sensor push endpoint for instant binary sensor updates.

Instead of waiting for the 5-second poll cycle, devices POST state changes
directly to this endpoint for near-instant binary_sensor updates in HA.
"""
from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import DashieCoordinator

_LOGGER = logging.getLogger(__name__)

# Fields that devices are allowed to push
_PUSHABLE_FIELDS = {"motionDetected", "faceDetected"}


class DashieSensorPushView(HomeAssistantView):
    """Receive instant sensor state pushes from devices."""

    url = "/api/dashie/sensor_push"
    name = "api:dashie:sensor_push"
    requires_auth = False  # Device authenticates via device_id matching

    async def post(self, request: web.Request) -> web.Response:
        """Handle a sensor push from a device."""
        hass: HomeAssistant = request.app["hass"]

        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"error": "invalid JSON"}, status=400
            )

        device_id = body.get("deviceId")
        if not device_id:
            return web.json_response(
                {"error": "deviceId required"}, status=400
            )

        # Find the coordinator for this device
        coordinator = _find_coordinator(hass, device_id)
        if not coordinator:
            return web.json_response(
                {"error": "unknown device"}, status=404
            )

        # Extract pushable sensor fields
        updates = {
            k: body[k] for k in _PUSHABLE_FIELDS if k in body
        }
        if not updates:
            return web.json_response(
                {"error": "no sensor fields provided"}, status=400
            )

        # Merge into coordinator data and notify entities immediately
        if coordinator.data:
            merged = dict(coordinator.data)
        else:
            merged = {}
        merged.update(updates)
        coordinator.async_set_updated_data(merged)

        _LOGGER.debug(
            "Sensor push from %s: %s", device_id, updates
        )
        return web.json_response({"status": "ok"})


def _find_coordinator(
    hass: HomeAssistant, device_id: str
) -> DashieCoordinator | None:
    """Find coordinator by device_id."""
    for value in hass.data.get(DOMAIN, {}).values():
        if isinstance(value, DashieCoordinator) and value.device_id == device_id:
            return value
    return None


def register_sensor_push_views(hass: HomeAssistant) -> None:
    """Register the sensor push HTTP view."""
    hass.http.register_view(DashieSensorPushView)
