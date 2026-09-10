#!/usr/bin/env bash
# Start EGO collection WiFi hotspot (no password prompt after hotspot-setup.sh).
set -euo pipefail

CONN="${EGO_HOTSPOT_CONN:-EGO-001-COLLECT}"
IFACE="${EGO_WIFI_IFACE:-wlp_usb_ap}"
MAX_ATTEMPTS="${EGO_HOTSPOT_MAX_ATTEMPTS:-8}"
RETRY_SLEEP="${EGO_HOTSPOT_RETRY_SLEEP:-5}"

if [[ -f /run/ego-hotspot/iface ]]; then
  IFACE="$(cat /run/ego-hotspot/iface)"
fi

_nmcli() {
  if [[ "${EUID}" -eq 0 ]]; then
    nmcli "$@"
  else
    sudo -n /usr/bin/nmcli "$@"
  fi
}

_ap_mode() {
  if command -v iw >/dev/null 2>&1 && iw dev "$IFACE" info 2>/dev/null | grep -q "type AP"; then
    return 0
  fi
  iwconfig "$IFACE" 2>/dev/null | grep -q "Mode:Master"
}

_hotspot_up() {
  local active_conn state
  state="$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: -v d="$IFACE" '$1==d { print $2; exit }')"
  if [[ "$state" == "unavailable" ]]; then
    return 1
  fi
  active_conn="$(
    nmcli -t -f DEVICE,CONNECTION device status 2>/dev/null \
      | awk -F: -v d="$IFACE" '$1==d { print $2; exit }'
  )"
  if [[ "$active_conn" == "$CONN" ]] && _ap_mode; then
    iwconfig "$IFACE" power off 2>/dev/null || true
    return 0
  fi
  if [[ -n "$active_conn" && "$active_conn" != "--" ]]; then
    _nmcli connection down "$active_conn" 2>/dev/null || true
    sleep 1
  fi
  _nmcli connection up "$CONN" ifname "$IFACE"
}

if [[ -x /home/server/ego-web/ego-wifi-radio-on.sh ]]; then
  /home/server/ego-web/ego-wifi-radio-on.sh || true
fi

_nmcli dev set "$IFACE" managed yes 2>/dev/null || true
ip link set "$IFACE" up 2>/dev/null || true

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  if _hotspot_up && _ap_mode; then
    logger -t ego-hotspot "Hotspot ${CONN} up on ${IFACE} (attempt ${attempt})"
    exit 0
  fi
  logger -t ego-hotspot "Hotspot bring-up attempt ${attempt} failed; retrying..."
  sleep "$RETRY_SLEEP"
done
logger -t ego-hotspot "Hotspot ${CONN} failed after ${MAX_ATTEMPTS} attempts"
exit 1
