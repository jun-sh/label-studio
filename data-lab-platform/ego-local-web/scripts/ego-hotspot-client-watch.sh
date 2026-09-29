#!/usr/bin/env bash
# Watch client join attempts on EGO USB AP (run on 130 while phone/tablet connects).
# Note: rtl8xxxu AP often presents EN10MB to tcpdump — use DHCP/ARP, not "wlan type mgt".
# Usage: sudo bash ego-hotspot-client-watch.sh [seconds]
set -euo pipefail

IFACE="${EGO_WIFI_IFACE:-wlp_usb_ap}"
[[ -f /run/ego-hotspot/iface ]] && IFACE="$(cat /run/ego-hotspot/iface)"
DURATION="${1:-120}"
LEASE="/var/lib/NetworkManager/dnsmasq-${IFACE}.leases"

echo "Watching ${IFACE} for ${DURATION}s (DHCP/ARP + any traffic)..."
echo "Leases before:"
cat "$LEASE" 2>/dev/null || echo "(empty)"
echo "--- tcpdump (connect the phone/tablet NOW) ---"
timeout "$DURATION" tcpdump -i "$IFACE" -n -v \
  'arp or (udp port 67 or udp port 68) or (ip and not host 192.168.8.1)' 2>&1 || true
echo "--- leases after ---"
cat "$LEASE" 2>/dev/null || echo "(empty)"
echo "--- hint ---"
echo "New DHCP line above = L2/L3 OK. No new lease + no DHCP in tcpdump = phone never joined this AP (wrong SSID/BSSID, still on SRI-WIFI, or out of range)."
