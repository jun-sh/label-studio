#!/usr/bin/env bash
# CI gate: fail if forbidden 20Hz capture artifacts remain in tree.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PATTERN='strict_20|20hz|20Hz|EGO_STRICT_20|--fps 20|imu-hz 100|FRAME_INTERVAL_MS=50|EXPECTED_FPS=20|strict20hz|iter_strict_20'

if rg -i "$PATTERN" \
  data-lab-platform/ego-stream-client \
  data-lab-platform/lerobot-studio \
  data-lab-platform/scripts \
  docs \
  --glob '!**/_deprecated/**' \
  --glob '!**/check-no-20hz.sh' \
  --glob '!**/z-production-egoverse.conf' \
  2>/dev/null; then
  echo "FAIL: 20Hz artifacts detected (EgoVerse 30/200 only)" >&2
  exit 1
fi

echo "OK: no forbidden 20Hz artifacts"
