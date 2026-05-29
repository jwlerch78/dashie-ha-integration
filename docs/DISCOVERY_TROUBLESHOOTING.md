# Discovery Troubleshooting Guide

This guide helps diagnose why Home Assistant isn't auto-discovering Dashie devices.

Dashie devices are discovered via **zeroconf / mDNS** (Bonjour). The tablet
advertises the service type `_dashie-kiosk._tcp.local.` on your network, and
Home Assistant's built-in `zeroconf` integration picks it up and offers the
device for setup.

> **Note:** Older builds used SSDP (UPnP) on UDP port 1900. Discovery now uses
> mDNS on UDP port 5353 (multicast group `224.0.0.251`). If you're following an
> older guide that talks about SSDP / port 1900 / `239.255.255.250`, it no
> longer applies.

## Quick Diagnosis

Verify the tablet's mDNS broadcast is visible from another machine on the same
network:

**macOS:**
```bash
dns-sd -B _dashie-kiosk._tcp
```

**Linux (avahi-utils):**
```bash
avahi-browse -r _dashie-kiosk._tcp
```

**Expected result:** Your Dashie tablet appears within a few seconds, resolving
to its IP and port (default `2323`).

If the tablet shows up here but Home Assistant doesn't discover it, the problem
is on the Home Assistant side (Docker networking, firewall, or the `zeroconf`
integration) rather than the tablet.

---

## Common Issues & Solutions

### 1. Docker Without Host Networking (Most Common)

**Problem:** Home Assistant running in Docker without `network_mode: host`
cannot send or receive mDNS multicast packets, so it never sees the tablet.

**Solution:** Edit your `docker-compose.yml`:

```yaml
services:
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:stable
    network_mode: host  # ← Add this line
    # ... rest of config
```

Then restart:
```bash
docker-compose down
docker-compose up -d
```

**Verify:** Check HA logs for zeroconf activity:
```bash
docker logs homeassistant 2>&1 | grep -i zeroconf
```

---

### 2. Zeroconf Integration Not Loaded

**Problem:** The `zeroconf` integration isn't running. It's part of
`default_config`, so it's enabled on almost every install — but a stripped-down
`configuration.yaml` may have removed it.

**Check:** Look in Home Assistant logs (`Settings > System > Logs`) for errors
referencing `homeassistant.components.zeroconf`.

**Solution:** Make sure either `default_config:` or `zeroconf:` is present in
`configuration.yaml`:
```yaml
default_config:
```
or, at minimum:
```yaml
zeroconf:
```

Then restart Home Assistant.

---

### 3. Firewall Blocking mDNS

**Problem:** A Linux firewall is blocking UDP port 5353 or multicast group
`224.0.0.251`.

**Solution:**

For **UFW**:
```bash
sudo ufw allow 5353/udp
sudo ufw allow from 224.0.0.251
```

For **iptables**:
```bash
sudo iptables -A INPUT -p udp --dport 5353 -j ACCEPT
sudo iptables -A INPUT -d 224.0.0.251 -j ACCEPT
```

For **firewalld** (the `mdns` service covers 5353/udp):
```bash
sudo firewall-cmd --permanent --add-service=mdns
sudo firewall-cmd --reload
```

---

### 4. Router Blocking Multicast (mDNS / IGMP Snooping)

**Problem:** Some routers filter multicast traffic between VLANs or between WiFi
and Ethernet segments, which blocks mDNS.

**Check:**
- Is your HA server on WiFi or Ethernet?
- Is the Dashie tablet on the same network segment?
- Are guest networks or VLANs isolating devices?

**Solutions:**
- Connect both HA and tablet to the same subnet
- Enable "mDNS reflector" / "Bonjour repeater" if your router/VLAN setup offers
  it (UniFi, pfSense/Avahi, etc.)
- Disable aggressive IGMP snooping in router settings (if available)
- Use manual configuration instead of auto-discovery (see below)

---

### 5. Integration Not Installed or Outdated

**Check Installation:**
1. SSH into Home Assistant
2. Check if the integration exists:
   ```bash
   ls -la /config/custom_components/dashie/
   ```

**Install/Update:**
1. Install via HACS (recommended) or copy the integration to
   `/config/custom_components/dashie/`
2. Restart Home Assistant
3. Check logs for errors

**Verify Version:**
```bash
cat /config/custom_components/dashie/manifest.json | grep version
```

---

### 6. Dashie API Not Enabled

The mDNS broadcast is published by the Dashie API service, so discovery only
works when the API is enabled.

**Check on Tablet:**
1. Open Dashie sidebar (swipe from left edge)
2. Go to **Settings > Developer**
3. Verify **Fully Kiosk API** is enabled

**Verify via ADB:**
```bash
adb shell "run-as com.dashieapp.Dashie cat /data/data/com.dashieapp.Dashie/shared_prefs/dashie_prefs.xml" | grep api_enabled
```

Should show: `<boolean name="api_enabled" value="true" />`

---

## Manual Configuration (Workaround)

If auto-discovery isn't working and you need to add the device now:

1. Find your tablet's IP address (shown in Dashie settings)
2. In Home Assistant:
   - Go to **Settings > Devices & Services**
   - Click **+ Add Integration**
   - Search for "Dashie"
   - Enter:
     - **Host:** `192.168.x.x` (tablet IP)
     - **Port:** `2323` (default)
     - **Password:** (if you set one)

---

## Enable Debug Logging

To see detailed discovery logs in Home Assistant, edit `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    homeassistant.components.zeroconf: debug
    custom_components.dashie: debug
```

Restart HA, then check logs:
```bash
# Look for zeroconf discovery events
docker logs homeassistant 2>&1 | grep -i zeroconf

# Look for Dashie integration logs
docker logs homeassistant 2>&1 | grep dashie
```

When the tablet is discovered, you should see messages like:
```
DEBUG (MainThread) [custom_components.dashie.config_flow] 🔍 Zeroconf discovery received!
DEBUG (MainThread) [custom_components.dashie.config_flow]   Name: SM-X200._dashie-kiosk._tcp.local.
DEBUG (MainThread) [custom_components.dashie.config_flow]   Type: _dashie-kiosk._tcp.local.
DEBUG (MainThread) [custom_components.dashie.config_flow]   Host: 192.168.x.x
DEBUG (MainThread) [custom_components.dashie.config_flow]   Port: 2323
```

---

## Advanced Debugging

### Resolve the service directly

Browse and resolve the Dashie service, printing its TXT records:

**macOS:**
```bash
dns-sd -L "SM-X200" _dashie-kiosk._tcp
```

**Linux:**
```bash
avahi-browse -rt _dashie-kiosk._tcp
```

**Expected:** The tablet resolves to `host:2323` and exposes TXT records
including `name` and `uuid`.

### Confirm the device API responds

Once you know the tablet IP, confirm the API the integration talks to is up:
```bash
curl "http://192.168.x.x:2323/?cmd=deviceInfo&type=json"
```

**Expected:** JSON with device fields (including `stableDeviceID` / `deviceID`).

### Monitor mDNS traffic

Use `tcpdump` to watch mDNS packets:
```bash
sudo tcpdump -i any -n port 5353
```

**Expected:** Periodic mDNS announcements referencing `_dashie-kiosk._tcp`.

---

## Still Not Working?

If you've tried everything above and discovery still doesn't work:

1. **Use manual configuration** as a workaround (see above)
2. **File an issue** at https://github.com/jwlerch78/dashie-ha-integration/issues with:
   - Output of `dns-sd -B _dashie-kiosk._tcp` (macOS) or
     `avahi-browse -r _dashie-kiosk._tcp` (Linux)
   - HA installation type (Docker/OS/Supervised/Core)
   - HA logs with debug logging enabled
   - Network setup (WiFi/Ethernet, VLANs, etc.)

---

## How Discovery Works (Reference)

1. The Dashie tablet advertises an mDNS service of type
   `_dashie-kiosk._tcp.local.`, with TXT records carrying `name` and `uuid`,
   resolving to the tablet's IP and API port (default `2323`).
2. Home Assistant's `zeroconf` integration sees the advertisement and hands it
   to the Dashie config flow (`async_step_zeroconf`).
3. The integration fetches `http://<tablet-ip>:2323/?cmd=deviceInfo&type=json`
   to read the device's stable ID and details, then offers it for setup. If the
   API requires a password, you're prompted for one.
