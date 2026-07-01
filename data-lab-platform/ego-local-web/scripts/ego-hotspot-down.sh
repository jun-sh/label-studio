#!/usr/bin/env bash
# Stop EGO collection WiFi hotspot.
set -euo pipefail
CONN="${EGO_HOTSPOT_CONN:-EGO-214-COLLECT}"
exec sudo -n /usr/bin/nmcli connection down "$CONN"
