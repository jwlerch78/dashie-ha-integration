# Dashie - Home Assistant Integration

A Home Assistant integration for [Dashie](https://dashieapp.com/guides/dashie-kiosk-features) — a free Android app for hosting Home Assistant dashboards on tablets, TVs, and kiosk displays. The integration auto-discovers your Dashie devices and exposes them as full Home Assistant devices with entities and services.

## Features

- **Auto-Discovery**: Devices are discovered automatically via zeroconf/mDNS (`_dashie-kiosk._tcp.local.`) on your network
- **Controls**: Screen on/off, kiosk lock, screensaver, dark mode, volume, brightness, reload dashboard, refresh WebView, bring to foreground, reboot, load URL
- **Sensors**: Battery, ambient light, brightness, memory/RAM usage, WiFi signal, and current page
- **Camera Stream**: View the tablet's own camera as a `camera` entity in other HA dashboards
- **Screenshot**: Capture what's currently displayed on the tablet screen as an `image` entity
- **Video Feeds**: Stream Frigate and other RTSP cameras *to* your tablets, with per-feed motion & face detection
- **Credential-Free Camera Streams**: Resolves camera RTSP URLs through your existing go2rtc (HA add-on, Frigate, or standalone) — or a bundled managed instance if none is found — so tablets play feeds without exposing camera credentials
- **Screensaver**: Show photos from your HA media folder, a URL, or a dedicated screensaver app
- **Voice & Timers**: Text-to-speech and up to 3 concurrent on-screen timers, controllable from HA automations
- **Media Player**: Control music playback via Music Assistant
- **Update Entity**: Surfaces new integration releases from GitHub

## Installation

### HACS (Recommended)

Dashie is in the default HACS store — no custom repository needed.

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jwlerch78&repository=dashie-ha-integration&category=integration)

1. Open **HACS** in Home Assistant
2. Search for **Dashie**
3. Click **Download**
4. Restart Home Assistant

<details>
<summary>On an older HACS that doesn't list Dashie? Add it as a custom repository</summary>

1. HACS → three dots (top right) → **Custom repositories**
2. Add `https://github.com/jwlerch78/dashie-ha-integration` with category **Integration**
3. Search for **Dashie** and download
</details>

### Manual Installation

1. Download the `custom_components/dashie` folder from this repository
2. Copy it to your Home Assistant's `custom_components` directory
3. Restart Home Assistant

## Setup

Once installed, Dashie devices on your network will be automatically discovered. You'll see a notification in Home Assistant to configure the device.

**Requirements:**
- Device must be on the same network as Home Assistant
- API must be enabled in Dashie settings

### Discovery Not Working?

If devices aren't being discovered automatically, see the [Discovery Troubleshooting Guide](docs/DISCOVERY_TROUBLESHOOTING.md).

You can also manually add devices via **Settings > Devices & Services > Add Integration > Dashie**.

## Entities

Each Dashie device creates the following entities:

### Sensors
| Entity | Description |
|--------|-------------|
| `sensor.{device}_battery` | Battery level (%) |
| `sensor.{device}_ambient_light` | Ambient light (lux) |
| `sensor.{device}_brightness` | Screen brightness |
| `sensor.{device}_memory` | Memory used |
| `sensor.{device}_ram_usage` | RAM usage |
| `sensor.{device}_current_page` | Current page / URL on screen |
| `sensor.{device}_wifi_signal` | WiFi signal strength |

### Binary Sensors
| Entity | Description |
|--------|-------------|
| `binary_sensor.{device}_plugged` | Plugged in / charging |
| `binary_sensor.{device}_screensaver_active` | Screensaver currently active |
| `binary_sensor.{device}_pin_set` | Lock PIN is set |

### Switches
| Entity | Description |
|--------|-------------|
| `switch.{device}_screen` | Turn screen on/off |
| `switch.{device}_screensaver` | Start/stop the screensaver |
| `switch.{device}_lock` | Enable/disable kiosk lock |
| `switch.{device}_dark_mode` | Toggle dashboard dark mode |

### Numbers
| Entity | Description |
|--------|-------------|
| `number.{device}_brightness` | Set screen brightness |
| `number.{device}_volume` | Set audio volume |

### Selects
| Entity | Description |
|--------|-------------|
| `select.{device}_screensaver_mode` | Screensaver mode (Dim, Black Overlay, Screen Off, URL, Photos) |

### Buttons
| Entity | Description |
|--------|-------------|
| `button.{device}_reload` | Reload the dashboard |
| `button.{device}_refresh_webview` | Refresh the WebView |
| `button.{device}_foreground` | Bring Dashie to front |
| `button.{device}_reboot_device` | Reboot the device |

### Text
| Entity | Description |
|--------|-------------|
| `text.{device}_load_url` | Load a URL on the tablet |
| `text.{device}_pin` | Set the kiosk lock PIN |

### Image
| Entity | Description |
|--------|-------------|
| `image.{device}_screenshot` | Current screenshot of the tablet display |

### Update
| Entity | Description |
|--------|-------------|
| `update.dashie_integration_update` | Notifies when a new integration release is available on GitHub |

### Camera (if RTSP enabled)
| Entity | Description |
|--------|-------------|
| `camera.{device}` | Live camera stream |

**Note about camera orientation**:
- **Snapshots**: Automatically rotated 180° and horizontally flipped to match RTSP stream
- **Live Stream**: The RTSP stream is un-mirrored at source using OpenGL filters (Android v2.21.9B+)
- **HA Native Player**: May still appear upside down due to RTSP rotation metadata being ignored

**For correct live stream orientation in HA**, use one of these options:

1. **WebRTC Card** (recommended): Install a WebRTC custom card like [WebRTC Camera](https://github.com/AlexxIT/WebRTC)
   - Respects RTSP rotation metadata
   - Shows correctly oriented video
   - Lower latency than HA's native player

2. **go2rtc with rotation** (for HA native player): Configure go2rtc to apply 180° rotation
   ```yaml
   go2rtc:
     streams:
       dashie_camera:
         - rtsp://[device-ip]:8554/
         - "ffmpeg:dashie_camera#video=h264#hardware#vflip"
   ```
   The `vflip` rotates 180° (stream is already un-mirrored at source). Then use `rtsp://localhost:8554/dashie_camera` as your camera source.

**Technical Details**: Dashie applies a horizontal flip filter using RootEncoder's RotationFilterRender to un-mirror the front camera at the source level. This provides correct orientation in all RTSP clients (VLC, WebRTC, etc.) without relying on metadata tags.

### Media Player
| Entity | Description |
|--------|-------------|
| `media_player.{device}` | Control music playback (play, pause, volume, next/prev) |

### Video Feeds (if configured)
| Entity | Description |
|--------|-------------|
| `camera.{device}_{feed}` | MJPEG proxy stream from Frigate or other RTSP sources |
| `binary_sensor.{device}_{feed}_motion` | Motion detection from camera feed |
| `binary_sensor.{device}_{feed}_face` | Face detection from camera feed |

## Services

All services are available under the `dashie.` domain and selectable from **Developer Tools → Actions**.

### Display & navigation
| Service | Description |
|---------|-------------|
| `dashie.load_url` | Navigate a device to a URL |
| `dashie.set_brightness` | Set screen brightness (0–100) |

### Audio & speech
| Service | Description |
|---------|-------------|
| `dashie.set_volume` | Set audio volume (0–10) |
| `dashie.speak` | Speak text on a device (text-to-speech) |

### Timers (unique to Dashie)
Internal timers — no Home Assistant timer helpers required. Up to 3 concurrent timers with automatic slot assignment.

| Service | Description |
|---------|-------------|
| `dashie.start_timer` | Start a timer and show it on all devices (e.g. `"5:00"`, `"1:30:00"`, `"5 minutes"`) |
| `dashie.pause_timer` | Pause or resume a timer (by `slot` 1–3 or `timer_id`; auto if only one running) |
| `dashie.cancel_timer` | Cancel a timer and hide it |

### Notifications & config
| Service | Description |
|---------|-------------|
| `dashie.show_message` | Display an overlay message for a given duration |
| `dashie.set_config` | Update shared config (Music Assistant / Immich tokens & URLs); devices pick up changes on reconnect |

### Advanced
| Service | Description |
|---------|-------------|
| `dashie.send_command` | Send a low-level command (see below) |

**`dashie.send_command` example:**

```yaml
action: dashie.send_command
data:
  device_id: kitchen_tablet
  command: loadStartUrl   # screenOn, screenOff, startScreensaver, stopScreensaver,
                          # loadStartUrl, restartApp, toForeground, lockKiosk,
                          # unlockKiosk, clearCache, clearWebstorage
```

**`dashie.start_timer` example:**

```yaml
action: dashie.start_timer
data:
  duration: "5:00"
  label: Pizza
```

## RTSP Stream Resolution

The integration can automatically resolve camera entity RTSP URLs for direct playback on tablets. When a tablet requests a camera stream:

1. Checks if go2rtc has a credential-free restream available
2. Auto-registers cameras in go2rtc if not already configured
3. Falls back to direct RTSP if no credentials are needed
4. Returns MJPEG as a last resort for cameras requiring authentication

This allows tablets to play camera feeds via ExoPlayer without exposing camera credentials.

## Troubleshooting

### Device not discovered

1. Ensure Dashie is running and connected to your network
2. Check that the API is enabled (Settings → System → Enable API)
3. Verify the device is on the same network/subnet as Home Assistant
4. Check Home Assistant logs for zeroconf/mDNS discovery messages

### Connection issues

1. Check the device's IP address hasn't changed
2. Verify the API port (default: 2323) is accessible
3. Try reloading the integration

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and how to deploy the integration to a Home Assistant instance for testing.

## License

MIT License - see [LICENSE](LICENSE) for details.
