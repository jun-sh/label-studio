#!/usr/bin/env bash
# MCAP Track2 acceptance (ego-mcap-track2). P4b H.264 remux derive checks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATION="${STATION_ID:-ego-mcap-track2}"
STUDIO="${SCRIPT_DIR}/../lerobot-studio"

log() { echo "[mcap-track2-rc] $*"; }

log "=== mcap derive unit tests (jpeg golden + h264 materialize) ==="
node --test "${STUDIO}/derive/mcap-reader.test.mjs" "${STUDIO}/derive/unit-mcap.test.mjs"

log "=== track2 station registry ==="
for f in \
  "${STUDIO}/config/collection-stations.json" \
  "${STUDIO}/config/collection-station-tokens.json" \
  "${STUDIO}/config/station-topology.json" \
  "${SCRIPT_DIR}/../config/ego-pipeline-stations.yaml"; do
  rg -q 'ego-mcap-track2' "$f" || { log "FAIL: missing ego-mcap-track2 in $f"; exit 1; }
done

log "=== P4b session discovery ==="
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SESSIONS_PY="${SCRIPT_DIR}/ego-pipeline-sessions.py"
python3 "${SESSIONS_PY}" slug ego-mcap-track2 --datalab-root "${DATALAB_ROOT}" | grep -q '^ego_mcap_track2$' \
  || { log "FAIL: ego-mcap-track2 slug"; exit 1; }

log "=== P4b watcher runtime ==="
bash -c "source ${SCRIPT_DIR}/ego-station-runtime.sh; ego_station_runtime_env ego-mcap-track2; test \"\${STREAM_INGEST_CONTAINER}\" = data-lab-stream-ingest-mcap-track2-1"

log "=== track2 meta bootstrap ==="
bash "${SCRIPT_DIR}/ego-mcap-track2-bootstrap-34.sh" >/dev/null

log "OK: mcap-track2-rc P4b passed"
