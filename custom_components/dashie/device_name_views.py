"""Device name endpoint for Dashie.

Returns HA device registry names for all Dashie devices so Android tablets
can look up their friendly name (e.g., "Mio 15\" Dashie") instead of using
Build.MODEL (e.g., "rk3576_u").

HTTP endpoint:
  GET /api/dashie/device/names — list all Dashie devices with name + model
"""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class DashieDeviceNamesView(HomeAssistantView):
    """Return friendly names for all Dashie devices from the HA device registry."""

    url = "/api/dashie/device/names"
    name = "api:dashie:device:names"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        device_registry = dr.async_get(hass)

        devices = []
        for device in device_registry.devices.values():
            dashie_ids = [
                id_tuple[1] for id_tuple in device.identifiers
                if id_tuple[0] == DOMAIN
            ]
            if not dashie_ids:
                continue
            devices.append({
                "device_id": device.id,
                "name": device.name_by_user or device.name or "",
                "model": device.model or "",
                "android_id": dashie_ids[0],
            })

        return web.json_response({"devices": devices})


def register_device_name_views(hass: HomeAssistant) -> None:
    """Register device name HTTP views."""
    hass.http.register_view(DashieDeviceNamesView())
    _LOGGER.info("Registered Dashie device name views")
