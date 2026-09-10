#!/usr/bin/env bash
# Connect built-in AX201 (wlo1) to lab WiFi for upstream internet.
set -euo pipefail

IFACE="${EGO_WLO1_IFACE:-wlo1}"
CLIENT_CONN="${EGO_WLO1_WIFI_CONN:-SRI-WIFI}"
MAX_ATTEMPTS="${EGO_WLO1_MAX_ATTEMPTS:-5}"
RETRY_SLEEP="${EGO_WLO1_RETRY_SLEEP:-5}"

log() { logger -t ego-wlo1-wifi "$*"; }

_nmcli() {
  if [[ "${EUID}" -eq 0 ]]; then
    nmcli "$@"
  else
    sudo -n /usr/bin/nmcli "$@"
  fi
}

if ! nmcli connection show "$CLIENT_CONN" &>/dev/null; then
  log "skip: NM profile ${CLIENT_CONN} not found"
  exit 0
fi

_nmcli dev set "$IFACE" managed yes 2>/dev/null || true
ip link set "$IFACE" up 2>/dev/null || true

active="$(
  nmcli -t -f DEVICE,CONNECTION device status 2>/dev/null \
    | awk -F: -v d="$IFACE" '$1==d { print $2; exit }'
)"
if [[ "$active" == "$CLIENT_CONN" ]]; then
  log "already connected ${CLIENT_CONN} on ${IFACE}"
  exit 0
fi

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  if _nmcli connection up "$CLIENT_CONN" ifname "$IFACE"; then
    log "connected ${CLIENT_CONN} on ${IFACE} (attempt ${attempt})"
    exit 0
  fi
  log "connect attempt ${attempt} failed for ${CLIENT_CONN} on ${IFACE}"
  sleep "$RETRY_SLEEP"
done

log "failed to connect ${CLIENT_CONN} on ${IFACE} after ${MAX_ATTEMPTS} attempts"
exit 1
