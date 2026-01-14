"""Constants for Dashie Lite integration."""

DOMAIN = "dashie"

# Config keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_PASSWORD = "password"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"

# Defaults
DEFAULT_PORT = 2323
DEFAULT_SCAN_INTERVAL = 15

# API endpoints (Fully Kiosk compatible)
API_DEVICE_INFO = "deviceInfo"
API_SCREEN_ON = "screenOn"
API_SCREEN_OFF = "screenOff"
API_LOAD_START_URL = "loadStartUrl"
API_LOAD_URL = "loadUrl"
API_BRING_TO_FOREGROUND = "toForeground"
API_RESTART_APP = "restartApp"
API_SET_BRIGHTNESS = "setStringSetting"
API_SET_VOLUME = "setAudioVolume"
API_LOCK_KIOSK = "lockKiosk"
API_UNLOCK_KIOSK = "unlockKiosk"
API_START_SCREENSAVER = "startScreensaver"
API_STOP_SCREENSAVER = "stopScreensaver"
API_TEXT_TO_SPEECH = "textToSpeech"
API_STOP_TEXT_TO_SPEECH = "stopTextToSpeech"
API_SET_PIN = "setPin"
API_CLEAR_PIN = "clearPin"

# SSDP
SSDP_ST = "urn:dashie:service:DashieLite:1"
