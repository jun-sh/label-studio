#!/usr/bin/env bash
# Wait until stream has committed frames (derive parquet path ready for postprocess).
set -euo pipefail

STATION="${STATION_ID:?STATION_ID required}"
HOST="${LABEL_STUDIO_HOST:-http://127.0.0.1:8080}"
HOST="${HOST%/}"
TIMEOUT="${EGO_WAIT_READY_SEC:-1800}"
INTERVAL="${EGO_WAIT_READY_INTERVAL_SEC:-5}"

deadline=$((SECONDS + TIMEOUT))
while (( SECONDS < deadline )); do
  frames="$(curl -sf "${HOST}/lerobot/api/stream/${STATION}/status" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('totalFrames',0))" 2>/dev/null || echo 0)"
  if [[ "${frames}" =~ ^[0-9]+$ && "${frames}" -gt 0 ]]; then
    echo "[wait-ready] ${STATION} totalFrames=${frames}"
    exit 0
  fi
  sleep "${INTERVAL}"
done
echo "[wait-ready] timeout waiting for ${STATION} stream data (${TIMEOUT}s)" >&2
exit 1
