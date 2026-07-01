#!/usr/bin/env bash
# Start EGO collection WiFi hotspot (no password prompt after hotspot-setup.sh).
set -euo pipefail
CONN="${EGO_HOTSPOT_CONN:-EGO-214-COLLECT}"
exec sudo -n /usr/bin/nmcli connection up "$CONN"
