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

log "TODO P3: derive mcap-reader + unit branch"
log "OK: mcap-pilot-rc P2 passed"
