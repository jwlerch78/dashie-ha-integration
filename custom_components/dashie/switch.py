"""Switch entities for Dashie Lite integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    API_SCREEN_ON,
    API_SCREEN_OFF,
    API_LOCK_KIOSK,
    API_UNLOCK_KIOSK,
    API_START_SCREENSAVER,
    API_STOP_SCREENSAVER,
    API_SET_DARK_MODE,
    API_SET_BOOLEAN_SETTING,
    API_START_RTSP_STREAM,
    API_STOP_RTSP_STREAM,
    SETTING_KEEP_SCREEN_ON,
    SETTING_AUTO_BRIGHTNESS,
    SETTING_START_ON_BOOT,
    SETTING_HIDE_SIDEBAR,
    SETTING_HIDE_HEADER,
    SETTING_RTSP_ENABLED,
    SETTING_RTSP_SOFTWARE_ENCODING,
)
from .coordinator import DashieCoordinator
from .entity import DashieEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie Lite switches."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]

    entities = [
        # =================================================================
        # CONTROLS (Primary - no category, shown prominently)
        # =================================================================
        DashieScreenSwitch(coordinator, device_id),
        DashieScreensaverSwitch(coordinator, device_id),
        DashieLockSwitch(coordinator, device_id),

        # =================================================================
        # DISPLAY (CONFIG category with "Display:" prefix)
        # =================================================================
        DashieKeepScreenOnSwitch(coordinator, device_id),
        DashieAutoBrightnessSwitch(coordinator, device_id),
        DashieDarkModeSwitch(coordinator, device_id),  # May be unavailable on Fire Tablets

        # =================================================================
        # CAMERA (CONFIG category with "Camera:" prefix)
        # =================================================================
        DashieRtspStreamSwitch(coordinator, device_id),
        DashieSoftwareEncodingSwitch(coordinator, device_id),

        # =================================================================
        # CONFIGURATION (CONFIG category with "Config:" prefix)
        # =================================================================
        DashieHideSidebarSwitch(coordinator, device_id),
        DashieHideTabsSwitch(coordinator, device_id),

        # =================================================================
        # SYSTEM (CONFIG category with "System:" prefix)
        # =================================================================
        DashieStartOnBootSwitch(coordinator, device_id),
    ]

    async_add_entities(entities)


class DashieScreenSwitch(DashieEntity, SwitchEntity):
    """Screen on/off switch - PRIMARY CONTROL."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:monitor"
    _attr_translation_key = "screen"
    # No EntityCategory = Primary control (shown prominently)

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_screen"
        self._attr_name = "Screen"

    @property
    def is_on(self) -> bool | None:
        """Return true if screen is on (not in black/off state)."""
        if self.coordinator.data:
            # isScreenOn is False only when in true "screen off" black mode
            return self.coordinator.data.get("isScreenOn", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the screen on (wake from black/off state)."""
        await self.coordinator.send_command(API_SCREEN_ON)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the screen off."""
        await self.coordinator.send_command(API_SCREEN_OFF)
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}
        is_device_admin = self.coordinator.data.get("isDeviceAdmin", False)
        return {
            "hardware_screen_off_available": is_device_admin,
            "screen_off_mode": "hardware" if is_device_admin else "overlay",
        }


class DashieScreensaverSwitch(DashieEntity, SwitchEntity):
    """Screensaver switch - PRIMARY CONTROL."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:sleep"
    _attr_translation_key = "screensaver"
    # No EntityCategory = Primary control (shown prominently)

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_screensaver"
        self._attr_name = "Screensaver"

    @property
    def is_on(self) -> bool | None:
        """Return true if screensaver is active."""
        if self.coordinator.data:
            return self.coordinator.data.get("isInScreensaver", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the screensaver."""
        await self.coordinator.send_command(API_START_SCREENSAVER)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the screensaver."""
        await self.coordinator.send_command(API_STOP_SCREENSAVER)
        await self.coordinator.async_request_refresh()


class DashieLockSwitch(DashieEntity, SwitchEntity):
    """Lock switch (PIN required if set)."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:lock"
    _attr_translation_key = "lock"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_lock"
        self._attr_name = "Lock"

    @property
    def is_on(self) -> bool | None:
        """Return true if kiosk is locked."""
        if self.coordinator.data:
            return self.coordinator.data.get("kioskLocked", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock the kiosk."""
        await self.coordinator.send_command(API_LOCK_KIOSK)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unlock the kiosk (API password is authentication, PIN not required)."""
        await self.coordinator.send_command(API_UNLOCK_KIOSK)
        await self.coordinator.async_request_refresh()


class DashieDarkModeSwitch(DashieEntity, SwitchEntity):
    """Dark mode switch."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:theme-light-dark"
    _attr_translation_key = "dark_mode"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_dark_mode"
        self._attr_name = "Dark Mode"

    @property
    def available(self) -> bool:
        """Return True if dark mode is supported on this device."""
        # First check parent availability (coordinator connected)
        if not super().available:
            _LOGGER.info("Dark mode: parent not available")
            return False
        if not self.coordinator.data:
            _LOGGER.info("Dark mode: no coordinator data")
            return False
        # Fire Tablets don't support dark mode (requires WRITE_SECURE_SETTINGS)
        supported = self.coordinator.data.get("supportsDarkMode", True)
        _LOGGER.info("Dark mode available check: supportsDarkMode=%s, data keys=%s",
                     supported, list(self.coordinator.data.keys())[:10])
        return supported

    @property
    def is_on(self) -> bool | None:
        """Return true if dark mode is enabled."""
        if self.coordinator.data:
            return self.coordinator.data.get("isDarkMode", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable dark mode."""
        await self.coordinator.send_command(API_SET_DARK_MODE, value="true")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable dark mode."""
        await self.coordinator.send_command(API_SET_DARK_MODE, value="false")
        await self.coordinator.async_request_refresh()


# =============================================================================
# Home Assistant Section (CONFIG category)
# =============================================================================


class DashieHideSidebarSwitch(DashieEntity, SwitchEntity):
    """Hide sidebar switch - hides the HA sidebar in the WebView."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:page-layout-sidebar-left"
    _attr_translation_key = "hide_sidebar"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_hide_sidebar"
        self._attr_name = "Hide Sidebar"

    @property
    def is_on(self) -> bool | None:
        """Return true if sidebar is hidden."""
        if self.coordinator.data:
            return self.coordinator.data.get("hideSidebar", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Hide the sidebar."""
        await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_HIDE_SIDEBAR, value="true"
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Show the sidebar."""
        await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_HIDE_SIDEBAR, value="false"
        )
        await self.coordinator.async_request_refresh()


class DashieHideTabsSwitch(DashieEntity, SwitchEntity):
    """Hide tabs switch - hides the HA header/tabs in the WebView."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:tab-remove"
    _attr_translation_key = "hide_tabs"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_hide_tabs"
        self._attr_name = "Hide Tabs"

    @property
    def is_on(self) -> bool | None:
        """Return true if tabs/header is hidden."""
        if self.coordinator.data:
            return self.coordinator.data.get("hideHeader", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Hide the tabs/header."""
        await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_HIDE_HEADER, value="true"
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Show the tabs/header."""
        await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_HIDE_HEADER, value="false"
        )
        await self.coordinator.async_request_refresh()


# =============================================================================
# Display Settings (CONFIG category)
# =============================================================================


class DashieKeepScreenOnSwitch(DashieEntity, SwitchEntity):
    """Keep screen on switch - prevents screensaver activation."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:monitor-eye"
    _attr_translation_key = "keep_screen_on"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_keep_screen_on"
        self._attr_name = "Keep Screen On"

    @property
    def is_on(self) -> bool | None:
        """Return true if keep screen on is enabled."""
        if self.coordinator.data:
            return self.coordinator.data.get("keepScreenOn", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable keep screen on."""
        await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_KEEP_SCREEN_ON, value="true"
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable keep screen on."""
        await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_KEEP_SCREEN_ON, value="false"
        )
        await self.coordinator.async_request_refresh()


class DashieAutoBrightnessSwitch(DashieEntity, SwitchEntity):
    """Auto brightness switch - uses ambient light sensor."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:brightness-auto"
    _attr_translation_key = "auto_brightness"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_auto_brightness"
        self._attr_name = "Auto Brightness"

    @property
    def available(self) -> bool:
        """Return True if auto brightness control is available."""
        if not super().available:
            return False
        if not self.coordinator.data:
            return False
        # Requires WRITE_SETTINGS permission on Android
        return self.coordinator.data.get("canControlBrightness", True)

    @property
    def is_on(self) -> bool | None:
        """Return true if auto brightness is enabled."""
        if self.coordinator.data:
            return self.coordinator.data.get("autoBrightness", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable auto brightness."""
        success = await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_AUTO_BRIGHTNESS, value="true"
        )
        if success:
            # Optimistic update for immediate UI feedback
            self.coordinator.update_local_data(autoBrightness=True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable auto brightness."""
        success = await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_AUTO_BRIGHTNESS, value="false"
        )
        if success:
            # Optimistic update for immediate UI feedback
            self.coordinator.update_local_data(autoBrightness=False)
        await self.coordinator.async_request_refresh()


# =============================================================================
# System Settings (CONFIG category)
# =============================================================================


class DashieStartOnBootSwitch(DashieEntity, SwitchEntity):
    """Start on boot switch - auto-launch app when device boots."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:power"
    _attr_translation_key = "start_on_boot"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_start_on_boot"
        self._attr_name = "Start on Boot"

    @property
    def is_on(self) -> bool | None:
        """Return true if start on boot is enabled."""
        if self.coordinator.data:
            return self.coordinator.data.get("startOnBoot", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable start on boot."""
        await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_START_ON_BOOT, value="true"
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable start on boot."""
        await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_START_ON_BOOT, value="false"
        )
        await self.coordinator.async_request_refresh()


class DashieRtspStreamSwitch(DashieEntity, SwitchEntity):
    """RTSP camera stream enable/disable switch."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:video"
    _attr_translation_key = "rtsp_stream"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_rtsp_stream"
        self._attr_name = "Camera Stream Enabled"

    @property
    def is_on(self) -> bool | None:
        """Return true if RTSP streaming is enabled (preference setting)."""
        if self.coordinator.data:
            # Read from rtspEnabled preference (not isStreaming which is actual state)
            return self.coordinator.data.get("rtspEnabled", False)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return RTSP stream attributes."""
        if not self.coordinator.data:
            return {}
        # Coordinator stores as rtsp_status (underscore), API returns streamUrl
        rtsp_status = self.coordinator.data.get("rtsp_status", {})
        if isinstance(rtsp_status, dict):
            return {
                "stream_url": rtsp_status.get("streamUrl"),
                "client_count": rtsp_status.get("clientCount", 0),
            }
        return {}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start RTSP streaming."""
        await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_RTSP_ENABLED, value="true"
        )
        self.coordinator.update_local_data(rtspEnabled=True)
        await self.coordinator.send_command(API_START_RTSP_STREAM)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop RTSP streaming."""
        await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_RTSP_ENABLED, value="false"
        )
        await self.coordinator.send_command(API_STOP_RTSP_STREAM)
        self.coordinator.update_local_data(rtspEnabled=False)
        await self.coordinator.async_request_refresh()


class DashieSoftwareEncodingSwitch(DashieEntity, SwitchEntity):
    """Software encoding switch for RTSP stream."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:cpu-32-bit"
    _attr_translation_key = "software_encoding"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_software_encoding"
        self._attr_name = "Camera Software Encoding"

    @property
    def is_on(self) -> bool | None:
        """Return true if software encoding is enabled."""
        if self.coordinator.data:
            # Prefer top-level rtspSoftwareEncoding, fallback to rtsp_config
            if "rtspSoftwareEncoding" in self.coordinator.data:
                return self.coordinator.data.get("rtspSoftwareEncoding", False)
            rtsp_config = self.coordinator.data.get("rtsp_config", {})
            if isinstance(rtsp_config, dict):
                return rtsp_config.get("softwareEncoding", False)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable software encoding."""
        success = await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_RTSP_SOFTWARE_ENCODING, value="true"
        )
        if success:
            self.coordinator.update_local_data(rtspSoftwareEncoding=True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable software encoding (use hardware encoding)."""
        success = await self.coordinator.send_command(
            API_SET_BOOLEAN_SETTING, key=SETTING_RTSP_SOFTWARE_ENCODING, value="false"
        )
        if success:
            self.coordinator.update_local_data(rtspSoftwareEncoding=False)
        await self.coordinator.async_request_refresh()
