#!/usr/bin/env bash
# Wait for USB WiFi AP iface + optional short boot delay before hotspot bring-up.
set -euo pipefail

IFACE="${EGO_WIFI_IFACE:-wlp_usb_ap}"
USB_MAC="${EGO_USB_WIFI_MAC:-50:2b:73:e8:46:0c}"
BOOT_DELAY="${EGO_HOTSPOT_BOOT_DELAY_SEC:-45}"
READY_TIMEOUT="${EGO_HOTSPOT_READY_TIMEOUT_SEC:-120}"

log() { logger -t ego-hotspot "$*"; }

wait_until() {
  local desc="$1"
  shift
  local deadline=$((SECONDS + READY_TIMEOUT))
  while (( SECONDS < deadline )); do
    if "$@"; then
      return 0
    fi
    sleep 2
  done
  log "boot-wait timeout: ${desc}"
  return 1
}

resolve_usb_iface() {
  local mac norm_mac dev
  if ip link show "$IFACE" &>/dev/null; then
    echo "$IFACE"
    return 0
  fi
  norm_mac="$(echo "$USB_MAC" | tr '[:upper:]' '[:lower:]')"
  for dev in /sys/class/net/*; do
    [[ -d "$dev" ]] || continue
    local name="${dev##*/}"
    [[ "$name" == wlo1 || "$name" == lo ]] && continue
    [[ "$name" == wlx* || "$name" == wlp_usb_ap ]] || continue
    local dev_mac
    dev_mac="$(cat "$dev/address" 2>/dev/null | tr '[:upper:]' '[:lower:]')" || continue
    if [[ "$dev_mac" == "$norm_mac" ]]; then
      echo "$name"
      return 0
    fi
  done
  for name in /sys/class/net/wlx*; do
    [[ -e "$name" ]] || continue
    echo "${name##*/}"
    return 0
  done
  return 1
}

nm_iface_available() {
  local state
  state="$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: -v d="$IFACE" '$1==d { print $2; exit }')"
  [[ -n "$state" && "$state" != "unavailable" ]]
}

if [[ -x /home/server/ego-web/ego-wifi-radio-on.sh ]]; then
  /home/server/ego-web/ego-wifi-radio-on.sh || true
fi

if resolved="$(resolve_usb_iface)"; then
  IFACE="$resolved"
  export EGO_WIFI_IFACE="$IFACE"
  mkdir -p /run/ego-hotspot
  echo "$IFACE" > /run/ego-hotspot/iface
fi

wait_until "iface ${IFACE} present" ip link show "$IFACE" &>/dev/null
wait_until "NM device ${IFACE} available" nm_iface_available

ip link set "$IFACE" up 2>/dev/null || true
nmcli dev set "$IFACE" managed yes 2>/dev/null || true

uptime_sec="$(awk '{print int($1)}' /proc/uptime)"
if (( uptime_sec < BOOT_DELAY )); then
  remain=$((BOOT_DELAY - uptime_sec))
  log "boot-wait sleeping ${remain}s (target ${BOOT_DELAY}s since boot)"
  sleep "$remain"
fi

log "boot-wait ready: ${IFACE}, boot_delay=${BOOT_DELAY}s"
