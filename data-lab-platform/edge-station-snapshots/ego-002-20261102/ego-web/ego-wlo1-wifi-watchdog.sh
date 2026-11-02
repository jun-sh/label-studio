#!/usr/bin/env bash
# Recover lab WiFi client (wlo1 / SRI-WIFI) when link drops after boot.
set -euo pipefail

IFACE="${EGO_WLO1_IFACE:-wlo1}"
CLIENT_CONN="${EGO_WLO1_WIFI_CONN:-SRI-WIFI}"
HOTSPOT_IFACE="${EGO_WIFI_IFACE:-wlp_usb_ap}"
UP_SCRIPT="${EGO_WLO1_UP_SCRIPT:-/home/server/ego-web/ego-wlo1-wifi-up.sh}"
RADIO_SCRIPT="${EGO_WIFI_RADIO_SCRIPT:-/home/server/ego-web/ego-wifi-radio-on.sh}"
RECONNECT_COOLDOWN="${EGO_WLO1_RECONNECT_COOLDOWN_SEC:-60}"
STATE_DIR="/run/ego-wlo1-wifi-watchdog"

log() { logger -t ego-wlo1-wifi-watchdog "$*"; }

lab_wifi_healthy() {
  local state active_conn
  state="$(
    nmcli -t -f DEVICE,STATE device status 2>/dev/null \
      | awk -F: -v d="$IFACE" '$1==d { print $2; exit }'
  )"
  [[ "$state" == "connected" ]] || return 1
  active_conn="$(
    nmcli -t -f DEVICE,CONNECTION device status 2>/dev/null \
      | awk -F: -v d="$IFACE" '$1==d { print $2; exit }'
  )"
  [[ "$active_conn" == "$CLIENT_CONN" ]] || return 1
  ip -4 addr show dev "$IFACE" 2>/dev/null | grep -q ' inet ' || return 1
}

bring_up() {
  if [[ -x "$RADIO_SCRIPT" ]]; then
    "$RADIO_SCRIPT" || true
  fi
  if [[ -x "$UP_SCRIPT" ]]; then
    "$UP_SCRIPT"
    return $?
  fi
  if [[ "${EUID}" -eq 0 ]]; then
    nmcli connection up "$CLIENT_CONN" ifname "$IFACE"
  else
    sudo -n /usr/bin/nmcli connection up "$CLIENT_CONN" ifname "$IFACE"
  fi
}

if [[ "$IFACE" == "$HOTSPOT_IFACE" ]]; then
  log "skip: lab wifi iface ${IFACE} matches hotspot iface ${HOTSPOT_IFACE}"
  exit 0
fi

mkdir -p "$STATE_DIR"

if lab_wifi_healthy; then
  exit 0
fi

log "unhealthy: $(nmcli -t -f DEVICE,STATE,CONNECTION device status 2>/dev/null | awk -F: -v d="$IFACE" '$1==d { print $1":"$2":"$3; exit }' || echo "${IFACE}:missing")"

now_ts="$(date +%s)"
if [[ -f "${STATE_DIR}/last_reconnect" ]]; then
  last="$(cat "${STATE_DIR}/last_reconnect")"
  if (( now_ts - last < RECONNECT_COOLDOWN )); then
    log "reconnect skipped (cooldown ${RECONNECT_COOLDOWN}s)"
    exit 1
  fi
fi

echo "$now_ts" > "${STATE_DIR}/last_reconnect"

if bring_up; then
  sleep 2
  if lab_wifi_healthy; then
    log "recovered ${CLIENT_CONN} on ${IFACE}"
    exit 0
  fi
fi

log "still unhealthy after recovery attempt"
exit 1
