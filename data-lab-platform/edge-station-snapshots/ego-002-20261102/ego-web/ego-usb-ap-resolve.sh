#!/usr/bin/env bash
# Resolve the USB WiFi interface used for EGO-001-COLLECT AP (not built-in wlo1).
# Prints iface name on stdout; exit 1 if none found.
set -euo pipefail

IFACE="${EGO_WIFI_IFACE:-wlp_usb_ap}"
USB_MAC="${EGO_USB_WIFI_MAC:-}"

_is_lab_wifi() {
  local name="$1"
  [[ "$name" == wlo1 || "$name" == lo || "$name" == p2p-dev-* ]]
}

_is_usb_wlan() {
  local name="$1"
  local devpath="/sys/class/net/${name}/device"
  [[ -L "$devpath" ]] || return 1
  readlink -f "$devpath" | grep -q '/usb[0-9]'
}

if ip link show "$IFACE" &>/dev/null; then
  echo "$IFACE"
  exit 0
fi

if [[ -n "$USB_MAC" ]]; then
  norm_mac="$(echo "$USB_MAC" | tr '[:upper:]' '[:lower:]')"
  for dev in /sys/class/net/*; do
    [[ -d "$dev" ]] || continue
    name="${dev##*/}"
    _is_lab_wifi "$name" && continue
    dev_mac="$(cat "$dev/address" 2>/dev/null | tr '[:upper:]' '[:lower:]')" || continue
    [[ "$dev_mac" == "$norm_mac" ]] || continue
    echo "$name"
    exit 0
  done
fi

for dev in /sys/class/net/wlx* /sys/class/net/wlp_usb_ap; do
  [[ -e "$dev" ]] || continue
  name="${dev##*/}"
  _is_lab_wifi "$name" && continue
  _is_usb_wlan "$name" || continue
  echo "$name"
  exit 0
done

exit 1
