# Dashie Lite - Home Assistant Integration

A Home Assistant custom integration for [Dashie Lite](https://www.dashieapp.com/dashie-lite-download), providing auto-discovery and control of Dashie Lite tablets on your network.

## Features

- **Auto-Discovery**: Devices are automatically discovered via SSDP when running on your network
- **Sensors**: Battery level, screen brightness, memory usage, connection health
- **Controls**: Screen on/off, kiosk lock, reload dashboard, bring to foreground
- **Camera**: View the device camera stream (requires RTSP streaming enabled)
- **Health Monitoring**: Track WebSocket connection status and performance metrics

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant
2. Click the three dots in the top right → Custom repositories
3. Add `https://github.com/dashieapp/dashie-ha-integration` with category "Integration"
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

## License

MIT License - see [LICENSE](LICENSE) for details.
