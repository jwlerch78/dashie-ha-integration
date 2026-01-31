# Quick Start Guide

Get the Dashie Camera Card running in 5 minutes.

## Step 1: Build the Card

```bash
cd /Users/johnlerch/projects/dashieapp_staging/.reference/build-plans/dashie-camera-card

# Install dependencies
npm install

# Build
npm run build
```

Output: `dist/dashie-camera-card.js`

## Step 2: Deploy to Home Assistant

### Option A: Copy to HA Server

```bash
# Copy to your Home Assistant www directory
scp dist/dashie-camera-card.js homeassistant@192.168.86.46:/config/www/
```

### Option B: Development Mode (Local)

```bash
# Serve locally
npm run serve
# Card available at: http://localhost:8080/dashie-camera-card.js
```

## Step 3: Add Resource in Home Assistant

1. Go to **Settings → Dashboards → Resources**
2. Click "+ Add Resource"
3. **URL:** `/local/dashie-camera-card.js` (or `http://localhost:8080/dashie-camera-card.js` for dev)
4. **Resource type:** JavaScript Module
5. Click "Create"

## Step 4: Add Card to Dashboard

```yaml
type: custom:dashie-camera
entity: camera.your_camera
title: Your Camera
```

## Step 5: Test on Samsung Tablet

1. Open Dashie Kiosk app on Samsung tablet
2. Navigate to dashboard with camera card
3. Check console (via Chrome remote debugging):
   ```
   [Dashie Camera Card] Platform detected: dashie-tablet
   [Dashie Camera Card] Selected protocol: hls
   [HLS Player] Loading stream: ...
   ```
4. Tap card to maximize

## Verify It's Working

### On Browser (Desktop/Mac)
- Card loads
- Shows browser badge (if `show_debug: true`)
- Stream plays (WebRTC or HLS)

### On Samsung Tablet
- Card loads
- Shows "dashie-tablet" badge (if `show_debug: true`)
- HLS stream plays (no codec error)
- Tap to maximize works

## Troubleshooting

### Card Not Appearing in Picker

1. Clear browser cache (Ctrl+Shift+R / Cmd+Shift+R)
2. Check browser console for errors
3. Verify resource is loaded: Dev Tools → Network → look for `dashie-camera-card.js`

### Stream Not Loading

1. **Check go2rtc:**
   ```bash
   curl http://192.168.86.46:1984/api/streams
   ```

2. **Check console logs:**
   ```
   [Dashie Camera Card] Stream URL: http://...
   [HLS Player] Loading stream: ...
   ```

3. **Try direct URL:**
   Open the stream URL in browser to verify it works

### Platform Detection Not Working

1. **Enable debug mode:**
   ```yaml
   show_debug: true
   ```

2. **Check for device bridge:**
   - Open Chrome DevTools on tablet (via ADB)
   - Console: `window.dashieDevice`
   - Should return object with `getSystemMetrics` method

3. **If bridge missing:**
   - Device bridge not implemented yet
   - Card falls back to browser mode
   - This is OK for testing - just use `protocol: hls` to force HLS

## Next Steps

### Test Maximize Feature

```yaml
type: custom:dashie-camera
entity: camera.your_camera
tap_action: maximize
```

Tap card → Should open fullscreen overlay

### Test with Frigate

```yaml
type: custom:dashie-camera
entity: camera.frigate_living_room
frigate:
  enabled: true
  url: http://192.168.86.46:5000
```

### Compare Performance

**Before (WebRTC card on Samsung tablet):**
- Codec error / black screen

**After (Dashie card on Samsung tablet):**
- HLS stream loads
- H.265 plays via MediaPlayer
- ✅ Working!

## Development Workflow

### Watch Mode (Auto-rebuild)

```bash
npm run watch
```

Edit files in `src/` → Auto-rebuilds to `dist/`

### Test Changes

1. Make code change
2. Wait for rebuild (npm run watch)
3. Refresh Home Assistant dashboard
4. Check console for new behavior

### Debug Console

**On Browser:**
- F12 → Console

**On Android Tablet:**
```bash
adb -s adb-R9JT10FWLQE-OWhRz2._adb-tls-connect._tcp shell
# Then use Chrome remote debugging
```

## Configuration Examples

### Minimal
```yaml
type: custom:dashie-camera
entity: camera.living_room
```

### With Title & Maximize
```yaml
type: custom:dashie-camera
entity: camera.living_room
title: Living Room
tap_action: maximize
```

### Forced HLS (For Testing)
```yaml
type: custom:dashie-camera
entity: camera.living_room
protocol: hls
go2rtc_url: http://192.168.86.46:1984
```

### Frigate Camera
```yaml
type: custom:dashie-camera
entity: camera.frigate_living_room
title: Living Room
frigate:
  enabled: true
  url: http://192.168.86.46:5000
```

### Debug Mode
```yaml
type: custom:dashie-camera
entity: camera.living_room
show_debug: true
```

Shows platform badge: "dashie-tablet" or "browser"

## Success Criteria

✅ Card builds without errors
✅ Card appears in Home Assistant card picker
✅ Stream loads on desktop browser
✅ Stream loads on Samsung tablet
✅ Tap to maximize works
✅ Platform detection shows correct badge
✅ Console shows HLS selected for tablet

## Need Help?

Check the main [README.md](./README.md) for detailed documentation.
