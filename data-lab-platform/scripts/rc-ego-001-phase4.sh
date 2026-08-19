#!/usr/bin/env bash
# Phase 4 acceptance: derive module (frame-map + IMU dual storage + mux).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STUDIO="${ROOT}/lerobot-studio"

log() { echo "[rc-ego-001-phase4] $*"; }

log "=== Phase 3 regression ==="
"${ROOT}/scripts/rc-ego-001-phase3.sh"

log "=== derive module unit tests ==="
cd "${STUDIO}"
node --test derive/derive.test.mjs

log "=== static: derive module files ==="
for f in pipeline.mjs frame-map.mjs parquet-writer.mjs mux-exec.mjs index.mjs; do
  test -f "derive/${f}"
done
test -f derive/imu/ingest-raw.py
test -f derive/imu/align-main.py

log "=== static: derive-pipeline delegates to derive/ ==="
rg -q 'derive/pipeline.mjs' "${STUDIO}/derive-pipeline.mjs"

log "Phase 4 acceptance PASSED"
log "Deploy 34: data-lab-platform/scripts/deploy-stream-ingest-v0.0.13-rc.4.sh"
