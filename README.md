# Dashie Lite - Home Assistant Integration

A Home Assistant custom integration for [Dashie Lite](https://www.dashieapp.com/dashie-lite-download), providing auto-discovery and control of Dashie Lite tablets on your network.

## Features

- **Camera Stream**: Enable RTSP streaming to view the tablets camera from other HA dashboards
- **Screen Savers**:  Shows photos from your Home Assistant My Media folder, URL, or dedicated screensaver app
- **Auto-Discovery**: Devices are automatically discovered via SSDP when running on your network
- **Sensors**: Ambient light sensor, screen brightness, memory usage, connection health
- **Controls**: Screen on/off, kiosk lock, volume, brightness, reload dashboard, bring to foreground
- **Health Monitoring**: Track WebSocket connection status and performance metrics

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant
2. Click the three dots in the top right → Custom repositories
3. Add `https://github.com/jwlerch78/dashie-ha-integration` with category "Integration"
4. Search for "Dashie Lite" and install
5. Restart Home Assistant

### Manual Installation

1. Download the `custom_components/dashie` folder from this repository
2. Copy it to your Home Assistant's `custom_components` directory
3. Restart Home Assistant

## Setup

Once installed, Dashie Lite devices on your network will be automatically discovered. You'll see a notification in Home Assistant to configure the device.

**Requirements:**
- Dashie Lite v2.21.0B or later (with SSDP support)
- Device must be on the same network as Home Assistant
- Fully Kiosk API must be enabled in Dashie Lite settings

### SSDP Discovery Not Working?

If devices aren't being discovered automatically, see the [SSDP Troubleshooting Guide](docs/SSDP_TROUBLESHOOTING.md).

You can also manually add devices via **Settings > Devices & Services > Add Integration > Dashie**.

## Entities

Each Dashie Lite device creates the following entities:

### Sensors
| Entity | Description |
|--------|-------------|
| `sensor.{device}_battery` | Battery level (%) |
| `sensor.{device}_brightness` | Screen brightness (0-255) |
| `sensor.{device}_connection_health` | WebSocket connection status |

### Switches
| Entity | Description |
|--------|-------------|
| `switch.{device}_screen` | Turn screen on/off |
| `switch.{device}_kiosk_lock` | Enable/disable kiosk lock |

### Buttons
| Entity | Description |
|--------|-------------|
| `button.{device}_reload` | Reload the dashboard |
| `button.{device}_bring_to_foreground` | Bring Dashie to front |

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

**Technical Details**: Dashie Lite v2.21.9B+ applies a horizontal flip filter using RootEncoder's RotationFilterRender to un-mirror the front camera at the source level. This provides correct orientation in all RTSP clients (VLC, WebRTC, etc.) without relying on metadata tags.

## Services

### `dashie.send_command`

Send a custom command to a Dashie Lite device.

```yaml
service: dashie.send_command
data:
  device_id: "kitchen_tablet"
  command: "loadUrl"
  value: "http://homeassistant.local:8123/dashboard-kitchen"
```

## Troubleshooting

### Device not discovered

1. Ensure Dashie Lite is running and connected to your network
2. Check that the Fully Kiosk API is enabled (Settings → System → Enable API)
3. Verify the device is on the same network/subnet as Home Assistant
4. Check Home Assistant logs for SSDP discovery messages

### Connection issues

1. Check the device's IP address hasn't changed
2. Verify the API port (default: 2323) is accessible
3. Try reloading the integration

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup instructions.

### Deploying Updates to Home Assistant

We use Samba to deploy the integration files from this repo to Home Assistant.

#### Prerequisites

1. **Samba add-on** must be installed and running in Home Assistant
   - Go to Settings → Add-ons → Add-on Store
   - Search for "Samba share" and install
   - Configure username/password and start the add-on

2. **Home Assistant IP**: `192.168.86.46`

#### Connecting via Samba (macOS)

1. Open **Finder**
2. Press **Cmd+K** (or Go → Connect to Server)
3. Enter: `smb://192.168.86.46/config`
4. Enter your Samba credentials when prompted
5. The HA config folder will mount at `/Volumes/config`

#### Copying Files

Once connected, run this command to update the integration:

```bash
# Copy all integration files to Home Assistant
cd /Users/johnlerch/projects/dashie-ha-integration/custom_components/dashie
find . -type f -exec cp {} /Volumes/config/custom_components/dashie/ \;
```

Or to do a clean install (removes old files first):

```bash
# Remove existing installation
rm -rf /Volumes/config/custom_components/dashie/*

# Copy fresh files
cd /Users/johnlerch/projects/dashie-ha-integration/custom_components/dashie
find . -type f -exec cp {} /Volumes/config/custom_components/dashie/ \;
```

#### After Deployment

**Restart Home Assistant** to load the updated integration:
- Go to Settings → System → Restart
- Or use Developer Tools → Services → `homeassistant.restart`

## License

MIT License - see [LICENSE](LICENSE) for details.
