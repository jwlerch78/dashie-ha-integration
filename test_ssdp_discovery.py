#!/usr/bin/env python3
"""
Test script to verify SSDP discovery is working for Dashie devices.

Run this on the same machine as Home Assistant to verify:
1. Multicast traffic is reaching the machine
2. Dashie SSDP broadcasts are being received
3. The broadcast format is correct

Usage:
    python3 test_ssdp_discovery.py

Expected output:
    Should show Dashie NOTIFY packets with device info
"""

import socket
import struct
import time
import sys


def listen_for_dashie_ssdp(duration=30):
    """Listen for Dashie SSDP NOTIFY broadcasts."""
    MCAST_GRP = '239.255.255.250'
    MCAST_PORT = 1900

    print(f"🔍 Listening for Dashie SSDP packets for {duration} seconds...")
    print(f"   Multicast address: {MCAST_GRP}:{MCAST_PORT}")
    print(f"   Looking for ST: urn:dashie:service:DashieLite:1 or urn:dashie:service:Dashie:1")
    print()

    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.bind(('', MCAST_PORT))
    except OSError as e:
        print(f"❌ ERROR: Cannot bind to port {MCAST_PORT}")
        print(f"   {e}")
        print(f"   Make sure no other SSDP listeners are running (like Home Assistant)")
        print(f"   Or run this script with sudo")
        return False

    # Join multicast group
    try:
        mreq = struct.pack('4sL', socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError as e:
        print(f"❌ ERROR: Cannot join multicast group")
        print(f"   {e}")
        return False

    sock.settimeout(1.0)

    start = time.time()
    dashie_devices_found = {}
    packet_count = 0

    try:
        while time.time() - start < duration:
            try:
                data, addr = sock.recvfrom(4096)
                packet_count += 1
                message = data.decode('utf-8', errors='ignore')

                # Check if it's a Dashie device
                if 'dashie' in message.lower():
                    # Parse device info
                    lines = message.split('\n')
                    device_info = {
                        'ip': addr[0],
                        'st': None,
                        'name': None,
                        'api': None,
                        'usn': None
                    }

                    for line in lines:
                        line = line.strip()
                        if line.startswith('NT:') or line.startswith('ST:'):
                            device_info['st'] = line.split(':', 1)[1].strip()
                        elif line.startswith('X-DASHIE-NAME:'):
                            device_info['name'] = line.split(':', 1)[1].strip()
                        elif line.startswith('X-DASHIE-API:'):
                            device_info['api'] = line.split(':', 1)[1].strip()
                        elif line.startswith('USN:'):
                            device_info['usn'] = line.split(':', 1)[1].strip()

                    # Store device (keyed by IP to dedupe)
                    if device_info['ip'] not in dashie_devices_found:
                        dashie_devices_found[device_info['ip']] = device_info
                        print(f"✅ Found Dashie device #{len(dashie_devices_found)}:")
                        print(f"   Name: {device_info['name']}")
                        print(f"   IP: {device_info['ip']}")
                        print(f"   API: {device_info['api']}")
                        print(f"   Service Type: {device_info['st']}")
                        print(f"   USN: {device_info['usn']}")
                        print()

            except socket.timeout:
                # Print progress dot every second
                elapsed = int(time.time() - start)
                if elapsed % 5 == 0:
                    sys.stdout.write('.')
                    sys.stdout.flush()
                continue

    except KeyboardInterrupt:
        print("\n⚠️  Interrupted by user")
    finally:
        sock.close()

    print(f"\n")
    print(f"📊 Results:")
    print(f"   Total SSDP packets received: {packet_count}")
    print(f"   Dashie devices found: {len(dashie_devices_found)}")

    if len(dashie_devices_found) == 0:
        print()
        print("❌ No Dashie devices found!")
        print()
        print("Troubleshooting:")
        print("  1. Check that the Dashie API is enabled on the tablet")
        print("     (Settings > Developer > Fully Kiosk API)")
        print("  2. Check that SSDP is enabled (enabled by default with API)")
        print("  3. Verify the tablet and this machine are on the same network")
        print("  4. Check if your router blocks multicast (IGMP snooping)")
        print("  5. Try rebooting the router if multicast seems broken")
        return False
    else:
        print()
        print("✅ Success! Dashie SSDP discovery is working.")
        print("   If Home Assistant still isn't discovering devices:")
        print("   1. Check HA logs for SSDP errors")
        print("   2. Restart Home Assistant")
        print("   3. Check if HA is running in Docker without host networking")
        print("   4. Enable debug logging for the Dashie integration")
        return True


if __name__ == '__main__':
    print("=" * 60)
    print("Dashie SSDP Discovery Test")
    print("=" * 60)
    print()

    success = listen_for_dashie_ssdp(duration=30)

    sys.exit(0 if success else 1)
