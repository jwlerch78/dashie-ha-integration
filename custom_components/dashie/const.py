"""Constants for Dashie Lite integration."""

DOMAIN = "dashie"

# Config keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_PASSWORD = "password"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_MEDIA_FOLDER = "media_folder"
CONF_MEDIA_BASE_PATH = "media_base_path"

# Defaults
DEFAULT_PORT = 2323
DEFAULT_SCAN_INTERVAL = 5
DEFAULT_MEDIA_FOLDER = "."  # Root of media folder
DEFAULT_MEDIA_BASE_PATH = ""  # Empty = use /config/media, otherwise absolute path

# =============================================================================
# API Endpoints
# =============================================================================

# Device Info
API_DEVICE_INFO = "deviceInfo"

# Screen & Display Control
API_SCREEN_ON = "screenOn"
API_SCREEN_OFF = "screenOff"
API_SET_BRIGHTNESS = "setStringSetting"  # key=screenBrightness, value=0-255
API_SET_DARK_MODE = "setDarkMode"

# Screensaver
API_START_SCREENSAVER = "startScreensaver"
API_STOP_SCREENSAVER = "stopScreensaver"
API_SET_SCREENSAVER_MODE = "setScreensaverMode"
API_SET_HA_MEDIA_FOLDER = "setHaMediaFolder"

# Navigation & URL
API_LOAD_START_URL = "loadStartUrl"
API_LOAD_URL = "loadUrl"

# App Control
API_BRING_TO_FOREGROUND = "toForeground"
API_RESTART_APP = "restartApp"
API_EXIT_APP = "exitApp"

# Kiosk Lock
API_LOCK_KIOSK = "lockKiosk"
API_UNLOCK_KIOSK = "unlockKiosk"
API_SET_PIN = "setPin"
API_CLEAR_PIN = "clearPin"

# Audio
API_SET_VOLUME = "setAudioVolume"
API_TEXT_TO_SPEECH = "textToSpeech"
API_STOP_TEXT_TO_SPEECH = "stopTextToSpeech"

# Settings (generic setters)
API_SET_STRING_SETTING = "setStringSetting"  # key, value
API_SET_BOOLEAN_SETTING = "setBooleanSetting"  # key, value (true/false)

# WebView & Cache
API_CLEAR_CACHE = "clearCache"
API_CLEAR_WEBSTORAGE = "clearWebstorage"

# Camera & RTSP
API_GET_CAMSHOT = "getCamshot"
API_START_RTSP_STREAM = "startRtspStream"
API_STOP_RTSP_STREAM = "stopRtspStream"
API_GET_RTSP_STATUS = "getRtspStatus"

# Motion Detection
API_TRIGGER_MOTION = "triggerMotion"

# =============================================================================
# Setting Keys (for setStringSetting/setBooleanSetting)
# =============================================================================

SETTING_KEEP_SCREEN_ON = "keepScreenOn"
SETTING_AUTO_BRIGHTNESS = "autoBrightness"
SETTING_SCREEN_BRIGHTNESS = "screenBrightness"
SETTING_SCREENSAVER_TIMEOUT = "screensaverTimeout"
SETTING_MOTION_WAKE_MODE = "motionWakeMode"
SETTING_START_ON_BOOT = "startOnBoot"
SETTING_RTSP_ENABLED = "rtspEnabled"
SETTING_HIDE_SIDEBAR = "hideSidebar"
SETTING_HIDE_HEADER = "hideHeader"  # "Hide Tabs" in UI
SETTING_HA_URL = "startUrl"  # Android API uses startUrl for setStringSetting
SETTING_API_PASSWORD = "apiPassword"
SETTING_ZOOM = "textScaling"  # WebView text/zoom scaling (50-200%)

# Camera/RTSP settings
SETTING_RTSP_FRAME_RATE = "rtspFrameRate"
SETTING_RTSP_RESOLUTION = "rtspResolution"
SETTING_RTSP_SOFTWARE_ENCODING = "rtspSoftwareEncoding"

# =============================================================================
# Enums & Constants
# =============================================================================

# Screensaver modes
SCREENSAVER_MODES = ["dim", "black", "url", "photos", "app"]

# Motion wake modes (matching Android enum)
MOTION_WAKE_MODES = {
    "disabled": "Disabled",
    "brightness": "Brightness Sensor",
    "camera": "Camera-based",
}

# SSDP discovery
SSDP_ST = "urn:dashie:service:DashieLite:1"
