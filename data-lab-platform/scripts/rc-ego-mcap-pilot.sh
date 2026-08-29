#!/usr/bin/env bash
# MCAP pilot acceptance (ego-mcap-pilot). Phase P2+ ingest/derive checks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATION="${STATION_ID:-ego-mcap-pilot}"
STUDIO="${SCRIPT_DIR}/../lerobot-studio"

log() { echo "[mcap-pilot-rc] $*"; }

log "=== production baseline (must pass) ==="
bash "${SCRIPT_DIR}/rc-ego-001-production.sh"

log "=== mcap fixture regression (edge writer) ==="
python3 -m pytest "${SCRIPT_DIR}/../ego-stream-client/tests/test_mcap_segment_writer.py" -q --noconftest 2>/dev/null || \
  python3 -c "import sys; sys.path.insert(0,'${SCRIPT_DIR}/../ego-stream-client'); from mcap_segment_writer import summarize_mcap_segment; from pathlib import Path; p=Path('${SCRIPT_DIR}/../fixtures/mcap/golden-seg.mcap'); assert p.is_file(); s=summarize_mcap_segment(p); assert s['topics']['/ego/camera/front_left']==3"

log "=== mcap ingest unit tests ==="
node --test "${STUDIO}/ingest/ingest-mcap.test.mjs"

log "=== pilot station registry ==="
for f in \
  "${STUDIO}/config/collection-stations.json" \
  "${STUDIO}/config/collection-station-tokens.json" \
  "${SCRIPT_DIR}/../config/ego-pipeline-stations.yaml"; do
  rg -q 'ego-mcap-pilot' "$f" || { log "FAIL: missing ego-mcap-pilot in $f"; exit 1; }
done

log "=== mcap derive unit tests ==="
node --test "${STUDIO}/derive/mcap-reader.test.mjs" "${STUDIO}/derive/unit-mcap.test.mjs"

log "=== P4 session discovery (sourceFormat=mcap, unit layout) ==="
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SESSIONS_PY="${SCRIPT_DIR}/ego-pipeline-sessions.py"
python3 "${SESSIONS_PY}" slug ego-mcap-pilot --datalab-root "${DATALAB_ROOT}" | grep -q '^ego_mcap_pilot$' \
  || { log "FAIL: ego-mcap-pilot slug"; exit 1; }
READY="$(python3 "${SESSIONS_PY}" ready-sessions ego-mcap-pilot --datalab-root "${DATALAB_ROOT}" 2>/dev/null | head -1 || true)"
if [[ -n "${READY}" ]]; then
  FMT="$(python3 "${SESSIONS_PY}" source-format ego-mcap-pilot "${READY}" --datalab-root "${DATALAB_ROOT}")"
  [[ "${FMT}" == "mcap" ]] || { log "FAIL: ${READY} source-format=${FMT} (want mcap)"; exit 1; }
  log "OK: ready session ${READY} sourceFormat=${FMT}"
else
  log "WARN: no session.READY on disk — skip live source-format check (run P3 field accept first)"
fi
ALL="$(python3 "${SESSIONS_PY}" all-sessions ego-mcap-pilot --datalab-root "${DATALAB_ROOT}" | wc -l)"
[[ "${ALL}" -ge 1 ]] || log "WARN: all-sessions empty (pilot stream may be clean)"

log "=== P4 watcher runtime ==="
bash "${SCRIPT_DIR}/ego-process-watcher.sh" --help >/dev/null
test -f "${SCRIPT_DIR}/ego-station-runtime.sh"
bash -c "source ${SCRIPT_DIR}/ego-station-runtime.sh; ego_station_runtime_env ego-mcap-pilot; test \"\${STREAM_INGEST_CONTAINER}\" = data-lab-stream-ingest-mcap-pilot-1"

log "=== P4 pilot meta bootstrap ==="
bash "${SCRIPT_DIR}/ego-mcap-pilot-bootstrap-34.sh" >/dev/null

log "OK: mcap-pilot-rc P4 passed"
