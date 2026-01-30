# SSDP Discovery Troubleshooting Guide

This guide helps diagnose why Home Assistant isn't discovering Dashie devices via SSDP.

## Quick Diagnosis

Run the test script to verify SSDP broadcasts are reaching your HA machine:

```bash
cd /path/to/dashie-ha-integration
python3 test_ssdp_discovery.py
```

**Expected result:** Should find your Dashie devices within 30 seconds.

If the test script finds devices but HA doesn't, the problem is with Home Assistant's SSDP integration, not the Dashie tablet.

---

## Common Issues & Solutions

### 1. Docker Without Host Networking (Most Common)

**Problem:** Home Assistant running in Docker without `network_mode: host` cannot receive multicast packets.

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

**Verify:** Check HA logs for SSDP messages:
```bash
docker logs homeassistant 2>&1 | grep -i ssdp
```

---

### 2. SSDP Integration Not Loaded

**Problem:** The `ssdp` integration is disabled or failing to load.

**Check:** Look in Home Assistant logs (`Settings > System > Logs`) for:
```
ERROR (MainThread) [homeassistant.components.ssdp]
```

**Solution:** Add to `configuration.yaml`:
```yaml
ssdp:
```

Then restart Home Assistant.

---

### 3. Firewall Blocking Multicast

**Problem:** Linux firewall blocking UDP port 1900 or multicast group 239.255.255.250.

**Solution:**

For **UFW**:
```bash
sudo ufw allow from 239.255.255.250
sudo ufw allow 1900/udp
```

For **iptables**:
```bash
sudo iptables -A INPUT -p udp --dport 1900 -j ACCEPT
sudo iptables -A INPUT -d 239.255.255.250 -j ACCEPT
```

For **firewalld**:
```bash
sudo firewall-cmd --permanent --add-port=1900/udp
sudo firewall-cmd --reload
```

---

### 4. Router Blocking Multicast (IGMP Snooping)

**Problem:** Some routers aggressively filter multicast traffic between VLANs or WiFi networks.

**Check:**
- Is your HA server on WiFi or Ethernet?
- Is the Dashie tablet on the same network segment?
- Are guest networks or VLANs isolating devices?

**Solutions:**
- Connect both HA and tablet to the same network
- Disable IGMP snooping in router settings (if available)
- Use manual configuration instead of SSDP discovery

---

### 5. Integration Not Installed or Outdated

**Check Installation:**
1. SSH into Home Assistant
2. Check if integration exists:
   ```bash
   ls -la /config/custom_components/dashie/
   ```

**Install/Update:**
1. Copy the integration to `/config/custom_components/dashie/`
2. Restart Home Assistant
3. Check logs for errors

**Verify Version:**
Check `manifest.json` version matches latest release:
```bash
cat /config/custom_components/dashie/manifest.json | grep version
```

---

### 6. Dashie API/SSDP Not Enabled

**Check on Tablet:**
1. Open Dashie sidebar (swipe from left edge)
2. Go to **Settings > Developer**
3. Verify:
   - ✅ **Fully Kiosk API** is enabled
   - ✅ **SSDP Discovery** is enabled (auto-enabled with API)

**Verify via ADB:**
```bash
adb shell "run-as com.dashieapp.Dashie.halite cat /data/data/com.dashieapp.Dashie.halite/shared_prefs/dashie_lite_prefs.xml" | grep api_enabled
```

Should show: `<boolean name="api_enabled" value="true" />`

---

## Manual Configuration (Workaround)

If SSDP discovery isn't working and you need to add the device now:

1. Find your tablet's IP address (shown in Dashie settings)
2. In Home Assistant:
   - Go to **Settings > Devices & Services**
   - Click **+ Add Integration**
   - Search for "Dashie"
   - Choose "Configure manually"
   - Enter:
     - **Host:** `192.168.x.x` (tablet IP)
     - **Port:** `2323` (default)
     - **Password:** (if you set one)

---

## Enable Debug Logging

To see detailed SSDP discovery logs in Home Assistant:

Edit `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    homeassistant.components.ssdp: debug
    custom_components.dashie: debug
```

Restart HA, then check logs:
```bash
# Look for SSDP discovery events
docker logs homeassistant 2>&1 | grep -i "SSDP discovery"

# Look for Dashie integration logs
docker logs homeassistant 2>&1 | grep dashie
```

You should see messages like:
```
WARNING (MainThread) [custom_components.dashie.config_flow] 🔍 SSDP discovery received!
WARNING (MainThread) [custom_components.dashie.config_flow]   Location: http://192.168.x.x:2323/?cmd=deviceInfo
WARNING (MainThread) [custom_components.dashie.config_flow]   ST: urn:dashie:service:DashieLite:1
```

---

## Advanced Debugging

### Test SSDP with netcat

Send an M-SEARCH query manually:
```bash
echo -e "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 3\r\nST: urn:dashie:service:DashieLite:1\r\n\r\n" | nc -u 239.255.255.250 1900
```

**Expected:** Response from tablet with device info.

### Monitor multicast traffic

Use `tcpdump` to watch SSDP traffic:
```bash
sudo tcpdump -i any -n port 1900
```

**Expected:** Should see NOTIFY packets from your Dashie tablet every 30 seconds.

### Check if multicast routing works

```bash
# Check multicast routes
ip mroute show

# Check if interface supports multicast
ip link show | grep -i multicast
```

---

## Still Not Working?

If you've tried everything above and SSDP still doesn't work:

1. **Use manual configuration** as a workaround (see above)
2. **File an issue** at https://github.com/jwlerch78/dashie-ha-integration/issues with:
   - Output of `test_ssdp_discovery.py`
   - HA installation type (Docker/OS/Supervised/Core)
   - HA logs with debug logging enabled
   - Network setup (WiFi/Ethernet, VLANs, etc.)

---

## Expected SSDP Packet Format

For reference, Dashie tablets broadcast this SSDP packet every 30 seconds:

```http
NOTIFY * HTTP/1.1
HOST: 239.255.255.250:1900
CACHE-CONTROL: max-age=1800
LOCATION: http://192.168.x.x:2323/?cmd=deviceInfo
NT: urn:dashie:service:DashieLite:1
NTS: ssdp:alive
SERVER: DashieLite/3.0.0B-halite UPnP/1.1
USN: uuid:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx::urn:dashie:service:DashieLite:1
X-DASHIE-NAME: SM-X200
X-DASHIE-API: http://192.168.x.x:2323/
X-DASHIE-HA-URL: http://192.168.y.y:8123
```

The integration matches on the `NT` header: `urn:dashie:service:DashieLite:1`
