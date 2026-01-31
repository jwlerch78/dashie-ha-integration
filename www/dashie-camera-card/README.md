# Dashie Camera Card

Adaptive camera card for Home Assistant with platform-aware streaming.

## Features

- ✅ **Platform Detection** - Auto-detects Dashie tablets vs browsers
- ✅ **Codec Aware** - Uses H.264/H.265 based on device support
- ✅ **HLS for Tablets** - Better codec support via Android MediaPlayer
- ✅ **WebRTC for Browsers** - Low-latency streaming for desktop
- ✅ **Maximize on Tap** - Full-screen camera viewing
- ✅ **Frigate Compatible** - Works with Frigate NVR cameras
- ✅ **Zero Configuration** - Auto-detects optimal settings

## Installation

### Prerequisites

```bash
npm install
```

### Build

```bash
# Development build with watch
npm run watch

# Production build
npm run build
```

Output: `dist/dashie-camera-card.js`

### Install in Home Assistant

1. Copy `dist/dashie-camera-card.js` to `/config/www/`

2. Add as a resource in Home Assistant:
   - Settings → Dashboards → Resources → Add Resource
   - URL: `/local/dashie-camera-card.js`
   - Resource type: JavaScript Module

3. Refresh your browser

## Usage

### Basic Configuration

```yaml
type: custom:dashie-camera
entity: camera.living_room
title: Living Room
```

### Advanced Configuration

```yaml
type: custom:dashie-camera
entity: camera.living_room
title: Living Room Camera

# Stream settings
quality: auto        # auto, high, medium, low, mobile
protocol: auto       # auto, webrtc, hls, rtsp
prefer_codec: auto   # auto, h264, h265

# Interactions
tap_action: maximize      # maximize, fullscreen, more-info, none
hold_action: none         # none, more-info, frigate-events
double_tap_action: none   # none, fullscreen, maximize

# go2rtc URL (auto-detected from Frigate if not specified)
go2rtc_url: http://192.168.1.100:1984

# Frigate integration
frigate:
  enabled: true
  url: http://192.168.1.100:5000
  show_events: true
  show_detections: false
  event_hours: 24

# Debug mode
show_debug: false
```

## How It Works

### Platform Detection

```
┌─────────────────────────────────────┐
│ Is window.dashieDevice available?  │
└─────────┬───────────────────────────┘
          │
     Yes  │  No
          ↓
    Dashie Tablet → Use HLS

          ↓ (No)

    Browser → Use WebRTC
```

### Stream Selection

**On Dashie Tablet:**
- Uses go2rtc HLS endpoint
- Leverages Android MediaPlayer codec support
- Supports H.265 natively (no WebRTC codec issues)

**On Browser:**
- Uses WebRTC for low latency
- Falls back to HLS if WebRTC unavailable

## Development

### Project Structure

```
dashie-camera-card/
├── src/
│   ├── dashie-camera-card.ts     # Main card component
│   ├── types.ts                  # TypeScript types
│   ├── platform-detector.ts      # Platform detection
│   └── players/
│       ├── hls-player.ts         # HLS playback
│       └── webrtc-player.ts      # WebRTC playback
├── dist/
│   └── dashie-camera-card.js     # Built output
├── package.json
├── tsconfig.json
├── rollup.config.js
└── README.md
```

### Testing

1. **Start development server:**
   ```bash
   npm run watch
   ```

2. **Serve locally:**
   ```bash
   npm run serve
   ```

3. **Test in Home Assistant:**
   - Point resource URL to `http://localhost:8080/dashie-camera-card.js`
   - Add card to dashboard
   - Check browser console for logs

### Debugging

Enable debug mode to see platform detection:

```yaml
type: custom:dashie-camera
entity: camera.living_room
show_debug: true
```

Check console logs:
```
[Dashie Camera Card] Platform detected: dashie-tablet
[Dashie Camera Card] Selected protocol: hls
[HLS Player] Loading stream: http://192.168.1.100:1984/api/stream.m3u8?src=living_room
```

## go2rtc Configuration

The card works best with go2rtc for stream management.

### Example go2rtc Config

```yaml
# /config/go2rtc.yaml
streams:
  living_room:
    - rtsp://username:password@192.168.1.100:554/stream1
    - "ffmpeg:living_room#video=h264#audio=opus"  # H.264 transcode

  garage:
    - rtsp://username:password@192.168.1.101:554/stream2
```

## Frigate Integration

Works seamlessly with Frigate NVR:

```yaml
type: custom:dashie-camera
entity: camera.frigate_living_room
title: Living Room
frigate:
  enabled: true
  url: http://192.168.1.100:5000
```

The card will auto-detect Frigate cameras and use go2rtc streams.

## Troubleshooting

### Stream Not Loading

1. **Check go2rtc is running:**
   ```bash
   curl http://192.168.1.100:1984/api/streams
   ```

2. **Verify stream URL:**
   - Browser console shows selected stream URL
   - Try accessing URL directly in browser

3. **Check CORS:**
   ```yaml
   # go2rtc config
   api:
     origin: "*"
   ```

### Black Screen on Tablet

1. **Verify platform detection:**
   - Enable `show_debug: true`
   - Should show "dashie-tablet" badge

2. **Check codec support:**
   - Open browser DevTools via ADB
   - Look for codec detection logs

3. **Try direct HLS URL:**
   ```yaml
   protocol: hls
   go2rtc_url: http://192.168.1.100:1984
   ```

### WebRTC Not Working

- WebRTC implementation is currently a stub
- Use `protocol: hls` as fallback
- Full WebRTC coming in Phase 2

## Roadmap

### Phase 1 (Current)
- [x] Platform detection
- [x] HLS player
- [x] WebRTC stub
- [x] Maximize on tap
- [ ] Codec detection integration

### Phase 2
- [ ] Full WebRTC implementation
- [ ] Quality tier selection
- [ ] Device metrics integration
- [ ] Frigate event overlay

### Phase 3
- [ ] Multi-camera grid
- [ ] PTZ controls
- [ ] Detection bounding boxes
- [ ] Event timeline

## License

MIT

## Support

- GitHub Issues: [Create an issue](https://github.com/yourrepo/dashie-camera-card/issues)
- Home Assistant Community: [Discussion thread](https://community.home-assistant.io/)
