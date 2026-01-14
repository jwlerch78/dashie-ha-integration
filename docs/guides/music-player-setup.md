# Setting Up Music Playback on Dashie Lite

*Play Spotify, Pandora, and other music through your tablet speakers*

Dashie Lite tablets make excellent ambient music players for kitchens, offices, and living spaces. This guide shows you how to set up Spotify (or other music services) to play through your tablet's speakers while keeping the Dashie dashboard visible on screen.

> ✓ **Good News:** This setup works even when Dashie Lite is locked in kiosk mode. Music plays in the background while your dashboard stays on screen.

---

## How It Works

Android allows music apps like Spotify to run as background services. When you start playback (from your phone, Home Assistant, or any Spotify Connect device), the music plays through the tablet's speakers even though Dashie Lite is the visible app.

```
┌─────────────────────────────────────────────────────────────────┐
│  Your Control Device                                              │
│  (Phone, HA Dashboard, Alexa, etc.)                             │
│                                                                  │
│  "Play music on Kitchen Tablet"                                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Spotify Cloud                                                    │
│                                                                  │
│  Routes audio stream to selected device                         │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Dashie Lite Tablet                                               │
│                                                                  │
│  ┌──────────────────────┐    ┌──────────────────────┐           │
│  │ Dashie Lite App      │    │ Spotify App          │           │
│  │ (Foreground)         │    │ (Background Service) │           │
│  │                      │    │                      │           │
│  │ Shows dashboard      │    │ Plays audio ───────────► 🔊     │
│  │ Calendar, weather... │    │                      │           │
│  └──────────────────────┘    └──────────────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

The key insight: **Dashie Lite's kiosk mode only prevents switching apps visually**. It doesn't stop background services like music playback from running.

---

## Prerequisites

### What You'll Need

- Dashie Lite installed on your Android tablet
- A Spotify Premium account (for Spotify Connect features)
- Google Play Store access on the tablet (to install Spotify)
- Optional: Home Assistant with Spotify integration (for HA dashboard control)

> ℹ️ **About Spotify Free:** Spotify Free accounts have limited remote control capabilities. For the best experience (controlling playback from your phone or Home Assistant), Spotify Premium is recommended.

---

## Part 1: Install Spotify on Your Tablet

First, we need to install the Spotify app on your Dashie Lite tablet.

### Step 1: Temporarily Exit Kiosk Mode

If Dashie Lite is locked in kiosk mode, you'll need to exit temporarily. In Dashie Lite, go to **Settings → Kiosk Mode** and tap **Disable Kiosk Lock**.

### Step 2: Open Google Play Store

Press the Home button or use the recent apps button to navigate to the Play Store. Search for "Spotify" and install it.

### Step 3: Sign In to Spotify

Open the Spotify app and sign in with your account. This registers the tablet as a Spotify Connect device on your network.

### Step 4: Name Your Device (Optional)

In Spotify settings, you can rename the device to something memorable like "Kitchen Tablet" or "Office Dashboard". This makes it easier to find when selecting playback devices.

### Step 5: Return to Dashie Lite

Open Dashie Lite and re-enable kiosk mode if desired. Spotify will continue running in the background and remain available as a playback target.

> ⚠️ **Battery Optimization:** Some tablets aggressively kill background apps to save battery. If music stops unexpectedly, go to **Settings → Apps → Spotify → Battery** and select "Unrestricted" or "Don't optimize".

---

## Part 2: Control Music from Your Phone

The simplest way to play music on your tablet is using Spotify Connect from your phone.

### Step 1: Open Spotify on Your Phone

Make sure both your phone and tablet are on the same WiFi network.

### Step 2: Tap the Device Icon

While playing or browsing music, tap the speaker/device icon at the bottom of the screen (or in the Now Playing view).

### Step 3: Select Your Tablet

You'll see a list of available Spotify Connect devices. Select your tablet (e.g., "Kitchen Tablet"). Music will immediately start playing through the tablet's speakers.

### Step 4: Control Playback

You can now control play/pause, skip, volume, and queue from your phone. The tablet just plays the audio—all controls are on your phone.

---

## Part 3: Control Music from Home Assistant (Optional)

For the ultimate smart home experience, you can control tablet music playback directly from your Home Assistant dashboard or via voice commands.

### Step 3.1: Set Up Spotify Integration in HA

1. **Add the Spotify Integration**

   In Home Assistant, go to **Settings → Devices & Services → Add Integration** and search for "Spotify". Follow the OAuth flow to connect your Spotify account.

2. **Verify Media Player Entity**

   After setup, you'll have a `media_player.spotify_your_name` entity. This entity can target any of your Spotify Connect devices, including your tablet.

### Step 3.2: Add a Spotify Card to Your Dashboard

Several excellent community cards provide Spotify control on your HA dashboard. Here are the most popular options:

| Card | Features | Best For |
|------|----------|----------|
| **Mini Media Player** | Compact, device selector, artwork | General use, space-efficient |
| **Spotify Card** | Full Spotify UI, playlists, search | Spotify-focused dashboards |
| **Mushroom Media Card** | Modern design, matches Mushroom theme | Mushroom-styled dashboards |

All of these can be installed via HACS (Home Assistant Community Store).

### Step 3.3: Example: Mini Media Player Card

```yaml
type: custom:mini-media-player
entity: media_player.spotify_john
artwork: cover
source: icon
sound_mode: icon
info: short
```

The `source` dropdown lets you select which device plays the music. Choose your tablet to route audio there.

### Step 3.4: Voice Control via HA

If you have voice assistants connected to Home Assistant (Alexa, Google Home, or local voice with Whisper), you can control tablet music with voice:

- *"Play jazz on the kitchen tablet"*
- *"Pause the music in the office"*
- *"Set kitchen tablet volume to 50%"*

> ✓ **Pro Tip:** Create an HA script that starts your favorite playlist on the tablet with a single button press or voice command. Perfect for morning routines!

---

## Other Music Services

While this guide focuses on Spotify, the same principle works with other music apps:

| Service | Remote Control | Notes |
|---------|---------------|-------|
| **Pandora** | Limited (Chromecast) | Cast to tablet if it supports Chromecast |
| **YouTube Music** | Chromecast | Works with Google Cast protocol |
| **Amazon Music** | Alexa Multi-Room | Requires Alexa app on tablet |
| **Apple Music** | Limited on Android | No AirPlay; manual control only |
| **Plex/Plexamp** | Plex Companion | Great for local music libraries |

Spotify has the best Spotify Connect ecosystem, which is why it's the recommended choice for this use case.

---

## Troubleshooting

### Tablet doesn't appear as a Spotify Connect device

- Ensure both devices are on the same WiFi network
- Open the Spotify app on the tablet briefly to "wake it up"
- Check that the tablet's Spotify is signed into the same account
- Restart the Spotify app on both devices

### Music stops playing after a while

- Disable battery optimization for Spotify (see warning above)
- Keep the tablet plugged in—some tablets kill background apps on battery
- In Spotify settings, enable "Keep Screen Awake During Playback" if available

### Audio quality is poor

- In Spotify settings, set streaming quality to "Very High"
- Ensure stable WiFi connection
- Consider connecting external speakers to the tablet for better sound

### Can't control volume from phone/HA

- Volume control depends on the device—some tablets require local volume adjustment
- If using Dashie HA integration, you can control tablet volume via the `number.volume` entity

---

## Summary

Setting up music on your Dashie Lite tablet is straightforward:

1. **Install Spotify** on the tablet and sign in
2. **Exempt from battery optimization** to prevent background killing
3. **Use Spotify Connect** from your phone to select the tablet as the playback device
4. **Optionally**, add Spotify integration to Home Assistant for dashboard and voice control

Your Dashie dashboard stays visible while music plays through the tablet's speakers—the perfect ambient display for any room in your home.

> 💡 **Next Steps:** Check out our [Voice Control Setup Guide](/guides/voice-control-setup) to add voice commands to your Dashie Lite experience, including "Play music on kitchen tablet"!

---

*Need help? [Open an issue](https://github.com/dashieapp/dashie-lite/issues) or email [support@dashieapp.com](mailto:support@dashieapp.com)*
