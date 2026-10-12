#!/usr/bin/env bash
# Ensure WiFi radios are unblocked (GPD/AX201 often soft-blocks WiFi at boot).
set -euo pipefail

log() { logger -t ego-wifi-radio "$*"; }

if command -v rfkill >/dev/null 2>&1; then
  rfkill unblock wifi 2>/dev/null || true
  rfkill unblock wlan 2>/dev/null || true
fi

if command -v nmcli >/dev/null 2>&1; then
  nmcli radio wifi on 2>/dev/null || true
fi

log "wifi radio on (rfkill unblock + nmcli radio wifi on)"
