#!/usr/bin/env bash
# Bind EGO-001-COLLECT to the current USB AP netdev (after dongle swap).
set -euo pipefail

CONN="${EGO_HOTSPOT_CONN:-EGO-001-COLLECT}"
RESOLVE="${EGO_USB_AP_RESOLVE:-/home/server/ego-web/ego-usb-ap-resolve.sh}"
DEFAULT_ENV="/etc/default/ego-hotspot"

log() { logger -t ego-usb-ap-bind "$*"; }

_nmcli() {
  if [[ "${EUID}" -eq 0 ]]; then
    nmcli "$@"
  else
    sudo -n /usr/bin/nmcli "$@"
  fi
}

if [[ ! -x "$RESOLVE" ]]; then
  RESOLVE="$(dirname "$0")/ego-usb-ap-resolve.sh"
fi

IFACE="$("$RESOLVE")"
mac="$(cat "/sys/class/net/${IFACE}/address" 2>/dev/null | tr '[:upper:]' '[:lower:]')"

mkdir -p /run/ego-hotspot
echo "$IFACE" > /run/ego-hotspot/iface

_nmcli connection modify "$CONN" connection.interface-name "$IFACE"
_nmcli dev set "$IFACE" managed yes 2>/dev/null || true
ip link set "$IFACE" up 2>/dev/null || true

if [[ -n "$mac" && -f "$DEFAULT_ENV" && "${EUID}" -eq 0 ]]; then
  if grep -q '^EGO_USB_WIFI_MAC=' "$DEFAULT_ENV"; then
    sed -i "s/^EGO_USB_WIFI_MAC=.*/EGO_USB_WIFI_MAC=${mac}/" "$DEFAULT_ENV"
  else
    echo "EGO_USB_WIFI_MAC=${mac}" >> "$DEFAULT_ENV"
  fi
elif [[ -n "$mac" && -f "$DEFAULT_ENV" ]]; then
  sudo -n sed -i "s/^EGO_USB_WIFI_MAC=.*/EGO_USB_WIFI_MAC=${mac}/" "$DEFAULT_ENV" 2>/dev/null || true
fi

log "bound ${CONN} -> ${IFACE} (mac ${mac:-unknown})"
echo "$IFACE"
