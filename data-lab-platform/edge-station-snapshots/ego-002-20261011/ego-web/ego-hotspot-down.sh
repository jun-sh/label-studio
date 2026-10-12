#!/usr/bin/env bash
# Stop EGO collection WiFi hotspot.
set -euo pipefail
CONN="${EGO_HOTSPOT_CONN:-EGO-001-COLLECT}"
if [[ "${EUID}" -eq 0 ]]; then
  exec nmcli connection down "$CONN"
fi
exec sudo -n /usr/bin/nmcli connection down "$CONN"
