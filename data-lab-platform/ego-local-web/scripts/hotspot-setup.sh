#!/usr/bin/env bash
# One-time hotspot + boot autostart on 214 (requires sudo).
# Run: sudo bash scripts/hotspot-setup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IFACE="${EGO_WIFI_IFACE:-wlp2s0}"
SSID="${EGO_HOTSPOT_SSID:-EGO-214-COLLECT}"
PSK="${EGO_HOTSPOT_PSK:-ego12345}"
GATEWAY="${EGO_HOTSPOT_GATEWAY:-192.168.4.1/24}"
CONN_NAME="${EGO_HOTSPOT_CONN:-EGO-214-COLLECT}"
DISABLE_LAB_WIFI_AUTO="${EGO_DISABLE_LAB_WIFI_AUTO:-1}"
HOTSPOT_PRIORITY="${EGO_HOTSPOT_AUTOCONNECT_PRIORITY:-100}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash $0" >&2
  exit 1
fi

if [[ -d /etc/polkit-1/rules.d ]]; then
  install -m 0644 "$SCRIPT_DIR/polkit/50-ego-hotspot.rules" /etc/polkit-1/rules.d/50-ego-hotspot.rules
  echo "Installed polkit JS rule: /etc/polkit-1/rules.d/50-ego-hotspot.rules"
else
  install -d -m 0755 /etc/polkit-1/localauthority/50-local.d
  install -m 0644 "$SCRIPT_DIR/polkit/50-ego-hotspot.pkla" /etc/polkit-1/localauthority/50-local.d/50-ego-hotspot.pkla
  echo "Installed polkit pkla rule: /etc/polkit-1/localauthority/50-local.d/50-ego-hotspot.pkla"
  echo "Note: polkit 0.105 on this host may ignore pkla; installing sudoers fallback."
fi

SUDOERS_FILE="/etc/sudoers.d/ego-hotspot"
cat > /tmp/ego-hotspot.sudoers <<EOF
# EGO local collection hotspot — nmcli only, no shell
server ALL=(ALL) NOPASSWD: /usr/bin/nmcli connection up ${CONN_NAME}, /usr/bin/nmcli connection down ${CONN_NAME}
EOF
install -m 0440 /tmp/ego-hotspot.sudoers "$SUDOERS_FILE"
rm -f /tmp/ego-hotspot.sudoers
echo "Installed sudoers: $SUDOERS_FILE"

install -d -m 0755 /home/server/ego-web
install -m 0755 "$SCRIPT_DIR/scripts/ego-hotspot-up.sh" /home/server/ego-web/ego-hotspot-up.sh
install -m 0755 "$SCRIPT_DIR/scripts/ego-hotspot-down.sh" /home/server/ego-web/ego-hotspot-down.sh
chown server:server /home/server/ego-web/ego-hotspot-*.sh 2>/dev/null || true

nmcli connection delete "$CONN_NAME" 2>/dev/null || true

nmcli connection add type wifi ifname "$IFACE" con-name "$CONN_NAME" \
  autoconnect yes \
  ssid "$SSID" \
  mode ap \
  ipv4.method shared \
  ipv4.addresses "$GATEWAY" \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "$PSK"

nmcli connection modify "$CONN_NAME" connection.autoconnect-priority "$HOTSPOT_PRIORITY"

if [[ "$DISABLE_LAB_WIFI_AUTO" == "1" ]]; then
  if nmcli connection show SRI-WIFI &>/dev/null; then
    nmcli connection modify SRI-WIFI connection.autoconnect no
    echo "Disabled autoconnect on SRI-WIFI (lab WiFi client; edge uses hotspot by default)."
  fi
fi

install -m 0644 "$SCRIPT_DIR/systemd/ecs-ego-hotspot.service" /etc/systemd/system/ecs-ego-hotspot.service
systemctl daemon-reload
systemctl enable --now ecs-ego-hotspot.service

# User services (ecs-ego-web) start at boot without login.
loginctl enable-linger server 2>/dev/null || true

echo "============================================"
echo "Hotspot profile: $CONN_NAME (boot autostart enabled)"
echo "  SSID:     $SSID"
echo "  Password: $PSK"
echo "  Gateway:  ${GATEWAY%/*}"
echo "  Web UI:   http://192.168.4.1:8080"
echo ""
echo "Systemd: ecs-ego-hotspot.service (enabled)"
echo "Manual:  /home/server/ego-web/ego-hotspot-up.sh | down.sh"
echo "============================================"
