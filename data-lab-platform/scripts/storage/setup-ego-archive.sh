#!/usr/bin/env bash
# Create cold archive root on 34 (7.7TB disk mount path).
set -euo pipefail
COLD_ROOT="${COLD_ROOT:-/media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/ego-archive}"
mkdir -p "$COLD_ROOT"/{ego-lan-214,logs}
chmod 775 "$COLD_ROOT" 2>/dev/null || true
echo "Cold archive ready: $COLD_ROOT"
df -h "$(dirname "$COLD_ROOT")" || true
