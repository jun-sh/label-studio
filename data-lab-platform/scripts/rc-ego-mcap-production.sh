#!/usr/bin/env bash
# MCAP production acceptance (ego-001 VPU H.264 + remux derive).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATION="${STATION_ID:-ego-001}"
STUDIO="${SCRIPT_DIR}/../lerobot-studio"

log() { echo "[mcap-rc] $*"; }

log "=== mcap derive unit tests ==="
node --test "${STUDIO}/derive/mcap-reader.test.mjs" "${STUDIO}/derive/unit-mcap.test.mjs"

log "=== ego-001 station registry ==="
for f in \
  "${STUDIO}/config/collection-stations.json" \
  "${STUDIO}/config/collection-station-tokens.json" \
  "${SCRIPT_DIR}/../config/ego-pipeline-stations.yaml"; do
  rg -q 'ego-001' "$f" || { log "FAIL: missing ego-001 in $f"; exit 1; }
  rg -q 'ego-mcap-track2' "$f" && { log "FAIL: stale ego-mcap-track2 in $f"; exit 1; }
done

log "=== watcher runtime ==="
bash -c "source ${SCRIPT_DIR}/ego-station-runtime.sh; ego_station_runtime_env ego-001; test \"\${STREAM_INGEST_CONTAINER}\" = data-lab-stream-ingest-1"

log "=== meta bootstrap ==="
bash "${SCRIPT_DIR}/ego-mcap-bootstrap-34.sh" >/dev/null

log "OK: mcap-rc ego-001 passed"
