#!/usr/bin/env bash
# MCAP pilot acceptance (ego-mcap-pilot). Phase P2+ fills ingest/derive checks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATION="${STATION_ID:-ego-mcap-pilot}"

log() { echo "[mcap-pilot-rc] $*"; }

log "=== production baseline (must pass) ==="
bash "${SCRIPT_DIR}/rc-ego-001-production.sh"

log "=== mcap fixture regression ==="
python3 -m pytest "${SCRIPT_DIR}/../ego-stream-client/tests/test_mcap_segment_writer.py" -q

log "=== pilot station registry ==="
for f in \
  "${SCRIPT_DIR}/../lerobot-studio/config/collection-stations.json" \
  "${SCRIPT_DIR}/../lerobot-studio/config/collection-station-tokens.json" \
  "${SCRIPT_DIR}/../config/ego-pipeline-stations.yaml"; do
  rg -q 'ego-mcap-pilot' "$f" || { log "FAIL: missing ego-mcap-pilot in $f"; exit 1; }
done

log "TODO P2: ingest receive-mcap + upload mcap.zst e2e for station=${STATION}"
log "OK: mcap-pilot-rc skeleton passed"
