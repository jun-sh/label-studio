#!/usr/bin/env bash
# Recover EGO collection hotspot when AP drops (iwlwifi AX201 or USB rtl8xxxu).
set -euo pipefail

IFACE="${EGO_WIFI_IFACE:-wlp_usb_ap}"
if [[ -f /run/ego-hotspot/iface ]]; then
  IFACE="$(cat /run/ego-hotspot/iface)"
fi
CONN="${EGO_HOTSPOT_CONN:-EGO-001-COLLECT}"
GATEWAY="${EGO_HOTSPOT_GATEWAY:-192.168.8.1}"
RELOAD_COOLDOWN="${EGO_HOTSPOT_RELOAD_COOLDOWN_SEC:-300}"
UP_SCRIPT="${EGO_HOTSPOT_UP_SCRIPT:-/home/server/ego-web/ego-hotspot-up.sh}"
STATE_DIR="/run/ego-hotspot-watchdog"

log() { logger -t ego-hotspot-watchdog "$*"; }

_nmcli() {
  if [[ "${EUID}" -eq 0 ]]; then
    nmcli "$@"
  else
    sudo -n /usr/bin/nmcli "$@"
  fi
}

uses_iwlwifi() {
  [[ "$IFACE" == wlo1 ]] && return 0
  local dev_path="/sys/class/net/${IFACE}/device/driver"
  [[ -L "$dev_path" ]] || return 1
  basename "$(readlink -f "$dev_path")" | grep -q '^iwlwifi'
}

hotspot_healthy() {
  local state
  state="$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: -v d="$IFACE" '$1==d { print $2; exit }')"
  [[ "$state" == "connected" ]] || return 1
  ip -4 addr show dev "$IFACE" 2>/dev/null | grep -q "inet ${GATEWAY}/" || return 1
  if command -v iw >/dev/null 2>&1 && iw dev "$IFACE" info 2>/dev/null | grep -q "type AP"; then
    return 0
  fi
  iwconfig "$IFACE" 2>/dev/null | grep -q "Mode:Master"
}

bring_up() {
  if [[ -x "$UP_SCRIPT" ]]; then
    "$UP_SCRIPT" || true
  else
    _nmcli connection up "$CONN" || true
  fi
}

reload_iwlwifi_if_allowed() {
  uses_iwlwifi || return 1
  local now_ts last
  now_ts="$(date +%s)"
  if [[ -f "${STATE_DIR}/last_driver_reload" ]]; then
    last="$(cat "${STATE_DIR}/last_driver_reload")"
    if (( now_ts - last < RELOAD_COOLDOWN )); then
      log "driver reload skipped (cooldown ${RELOAD_COOLDOWN}s)"
      return 1
    fi
  fi
  log "reloading iwlwifi after microcode errors"
  _nmcli connection down "$CONN" 2>/dev/null || true
  sleep 2
  modprobe -r iwlmvm 2>/dev/null || true
  modprobe -r iwlwifi 2>/dev/null || true
  sleep 2
  modprobe iwlwifi 2>/dev/null || true
  sleep 5
  mkdir -p "$STATE_DIR"
  echo "$now_ts" > "${STATE_DIR}/last_driver_reload"
  return 0
}

mkdir -p "$STATE_DIR"

if hotspot_healthy; then
  iwconfig "$IFACE" power off 2>/dev/null || true
  exit 0
fi

log "unhealthy: $(nmcli -t -f DEVICE,STATE device status 2>/dev/null | grep "^${IFACE}:" || echo "${IFACE}:missing")"

microcode_recent=false
if uses_iwlwifi && journalctl -k --since "2 min ago" 2>/dev/null | grep -q "Microcode SW error"; then
  microcode_recent=true
  log "recent Microcode SW error — nmcli bounce before bring-up"
  _nmcli connection down "$CONN" 2>/dev/null || true
  sleep 2
fi

bring_up
sleep 2
if hotspot_healthy; then
  log "recovered via hotspot-up"
  exit 0
fi

if $microcode_recent && reload_iwlwifi_if_allowed; then
  bring_up
  sleep 2
  if hotspot_healthy; then
    log "recovered after driver reload"
    exit 0
  fi
fi

log "still unhealthy after recovery attempts"
exit 1
