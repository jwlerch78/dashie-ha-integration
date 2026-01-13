"""Constants for Dashie Lite integration."""

DOMAIN = "dashie"

# Config keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"

# Defaults
DEFAULT_PORT = 2323
DEFAULT_SCAN_INTERVAL = 30

# API endpoints (Fully Kiosk compatible)
API_DEVICE_INFO = "deviceInfo"
API_SCREEN_ON = "screenOn"
API_SCREEN_OFF = "screenOff"
API_LOAD_START_URL = "loadStartUrl"
API_BRING_TO_FOREGROUND = "toForeground"
API_SET_BRIGHTNESS = "setStringSetting"
API_SET_VOLUME = "setAudioVolume"
API_LOCK_KIOSK = "lockKiosk"
API_UNLOCK_KIOSK = "unlockKiosk"

# SSDP
SSDP_ST = "urn:dashie:service:DashieLite:1"
