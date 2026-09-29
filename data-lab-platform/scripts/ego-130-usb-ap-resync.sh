#!/usr/bin/env bash
# Push USB AP scripts + udev to 130 and rebind EGO-001-COLLECT to the plugged dongle.
# Usage: ego-130-usb-ap-resync.sh [ssh_target]
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EGO_WEB="${ROOT}/ego-local-web"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"

exec "${ROOT}/scripts/ego-130-provision-usb-hotspot.sh" "$TARGET" wlp_usb_ap
