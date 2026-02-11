"""Sensor entities for Dashie Lite integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation, LIGHT_LUX
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_DEVICE_ID
from .coordinator import DashieCoordinator
from .entity import DashieEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie Lite sensors."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]

    entities = [
        # =================================================================
        # SENSORS (Primary - no category, shown in Sensors section)
        # =================================================================
        DashieLightSensor(coordinator, device_id),

        # =================================================================
        # CAMERA INFO (DIAGNOSTIC category - read-only camera settings)
        # =================================================================
        DashieCameraFrameRateSensor(coordinator, device_id),
        DashieCameraResolutionSensor(coordinator, device_id),
        DashieCameraStreamUrlSensor(coordinator, device_id),

        # =================================================================
        # STATUS (DIAGNOSTIC category, shown in Status section)
        # =================================================================
        DashieAndroidVersionSensor(coordinator, device_id),
        DashieAppVersionSensor(coordinator, device_id),
        DashieBatterySensor(coordinator, device_id),
        DashieCurrentPageSensor(coordinator, device_id),
        DashieDeviceIdSensor(coordinator, device_id),
        DashieRamUsageSensor(coordinator, device_id),
        DashieStorageSensor(coordinator, device_id),
        DashieWifiSignalSensor(coordinator, device_id),
    ]

    async_add_entities(entities)


class DashieBatterySensor(DashieEntity, SensorEntity):
    """Battery level sensor."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "battery"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_battery"
        self._attr_name = "Battery"

    @property
    def native_value(self) -> int | None:
        """Return the battery level."""
        if self.coordinator.data:
            return self.coordinator.data.get("batteryLevel")
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional battery attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "plugged": self.coordinator.data.get("plugged"),
        }


class DashieLightSensor(DashieEntity, SensorEntity):
    """Ambient light sensor - shown in Sensors section."""

    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_native_unit_of_measurement = LIGHT_LUX
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:brightness-5"
    _attr_translation_key = "light"
    # No EntityCategory = shown in Sensors section (not Status)

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_light"
        self._attr_name = "Ambient Light"

    @property
    def native_value(self) -> int | None:
        """Return the ambient light level in lux."""
        if self.coordinator.data:
            return self.coordinator.data.get("ambientLight")
        return None


class DashieCurrentPageSensor(DashieEntity, SensorEntity):
    """Current page/URL sensor."""

    _attr_icon = "mdi:web"
    _attr_translation_key = "current_page"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_current_page"
        self._attr_name = "Current Page"

    @property
    def native_value(self) -> str | None:
        """Return the current page URL."""
        if self.coordinator.data:
            return self.coordinator.data.get("currentPage")
        return None


class DashieWifiSignalSensor(DashieEntity, SensorEntity):
    """WiFi signal strength sensor."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi"
    _attr_translation_key = "wifi_signal"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_wifi_signal"
        self._attr_name = "WiFi Signal"

    @property
    def native_value(self) -> int | None:
        """Return the WiFi signal strength as percentage."""
        if self.coordinator.data:
            return self.coordinator.data.get("wifiSignalLevel")
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional WiFi attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "ssid": self.coordinator.data.get("ssid"),
            "ip_address": self.coordinator.data.get("ip4"),
            "mac_address": self.coordinator.data.get("Mac"),
        }


class DashieStorageSensor(DashieEntity, SensorEntity):
    """Internal storage sensor."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:harddisk"
    _attr_translation_key = "storage"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_storage"
        self._attr_name = "Storage Free"

    @property
    def native_value(self) -> float | None:
        """Return the free storage in GB."""
        if self.coordinator.data:
            free_bytes = self.coordinator.data.get("internalStorageFreeSpace")
            if free_bytes is not None:
                return round(free_bytes / (1024 ** 3), 2)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional storage attributes."""
        if not self.coordinator.data:
            return {}
        total_bytes = self.coordinator.data.get("internalStorageTotalSpace")
        return {
            "total_gb": round(total_bytes / (1024 ** 3), 2) if total_bytes else None,
        }


class DashieDeviceIdSensor(DashieEntity, SensorEntity):
    """Device ID sensor - unique identifier for the device."""

    _attr_icon = "mdi:identifier"
    _attr_translation_key = "device_id"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_device_id"
        self._attr_name = "Device ID"

    @property
    def native_value(self) -> str | None:
        """Return the device ID."""
        if self.coordinator.data:
            return self.coordinator.data.get("deviceID")
        return None


class DashieRamUsageSensor(DashieEntity, SensorEntity):
    """System RAM usage percentage sensor.

    Shows the same RAM % as the performance overlay - system-wide RAM usage
    calculated from ActivityManager.MemoryInfo (total - available) / total.
    """

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:memory"
    _attr_translation_key = "ram_usage"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_ram_usage"
        self._attr_name = "RAM Usage"

    @property
    def native_value(self) -> int | None:
        """Return the system RAM usage percentage."""
        if self.coordinator.data:
            return self.coordinator.data.get("ramUsedPercent")
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional RAM attributes."""
        if not self.coordinator.data:
            return {}
        attrs = {}
        total_mb = self.coordinator.data.get("ramTotalMb")
        available_mb = self.coordinator.data.get("ramAvailableMb")
        app_memory_mb = self.coordinator.data.get("appMemoryMb")
        if total_mb:
            attrs["total_mb"] = total_mb
        if available_mb:
            attrs["available_mb"] = available_mb
        if app_memory_mb:
            attrs["app_pss_mb"] = app_memory_mb
        return attrs


class DashieAndroidVersionSensor(DashieEntity, SensorEntity):
    """Android version sensor - OS version of the device."""

    _attr_icon = "mdi:android"
    _attr_translation_key = "android_version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_android_version"
        self._attr_name = "Android Version"

    @property
    def native_value(self) -> str | None:
        """Return the Android version."""
        if self.coordinator.data:
            return self.coordinator.data.get("androidVersion")
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional device attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "device_model": self.coordinator.data.get("deviceModel"),
            "device_manufacturer": self.coordinator.data.get("deviceManufacturer"),
        }


class DashieAppVersionSensor(DashieEntity, SensorEntity):
    """App version sensor - Dashie Lite version."""

    _attr_icon = "mdi:application-cog"
    _attr_translation_key = "app_version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_app_version"
        self._attr_name = "App Version"

    @property
    def native_value(self) -> str | None:
        """Return the app version."""
        if self.coordinator.data:
            return self.coordinator.data.get("appVersionName")
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional version attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "version_code": self.coordinator.data.get("appVersionCode"),
        }


# =============================================================================
# CAMERA INFO (DIAGNOSTIC category - Camera settings are read-only sensors)
# =============================================================================


class DashieCameraFrameRateSensor(DashieEntity, SensorEntity):
    """Camera frame rate sensor."""

    _attr_icon = "mdi:filmstrip"
    _attr_translation_key = "camera_frame_rate"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "fps"

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_camera_frame_rate"
        self._attr_name = "Camera Frame Rate"

    @property
    def native_value(self) -> int | None:
        """Return the camera frame rate."""
        if self.coordinator.data:
            # Frame rate comes from rtsp_config (getRtspConfig API), field is "fps"
            rtsp_config = self.coordinator.data.get("rtsp_config", {})
            if isinstance(rtsp_config, dict):
                return rtsp_config.get("fps")
        return None


class DashieCameraResolutionSensor(DashieEntity, SensorEntity):
    """Camera resolution sensor."""

    _attr_icon = "mdi:monitor-screenshot"
    _attr_translation_key = "camera_resolution"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_camera_resolution"
        self._attr_name = "Camera Resolution"

    @property
    def native_value(self) -> str | None:
        """Return the camera resolution."""
        if self.coordinator.data:
            # Resolution comes from rtsp_config (getRtspConfig API)
            rtsp_config = self.coordinator.data.get("rtsp_config", {})
            if isinstance(rtsp_config, dict):
                width = rtsp_config.get("width")
                height = rtsp_config.get("height")
                if width and height:
                    return f"{width}x{height}"
        return None


class DashieCameraStreamUrlSensor(DashieEntity, SensorEntity):
    """Camera stream URL sensor."""

    _attr_icon = "mdi:link"
    _attr_translation_key = "camera_stream_url"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DashieCoordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_camera_stream_url"
        self._attr_name = "Camera Stream URL"

    @property
    def native_value(self) -> str | None:
        """Return the RTSP stream URL."""
        if self.coordinator.data:
            # Stream URL comes from rtsp_status (getRtspStatus API), field is "streamUrl"
            rtsp_status = self.coordinator.data.get("rtsp_status", {})
            if isinstance(rtsp_status, dict):
                return rtsp_status.get("streamUrl")
        return None
