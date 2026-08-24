#!/usr/bin/env bash
# Phase 3 acceptance: 34 ingest module (raw-first + segment state + DONE_UPLOAD gate).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STUDIO="${ROOT}/lerobot-studio"

log() { echo "[rc-ego-001-phase3] $*"; }

log "=== Phase 2 regression (130) ==="
"${ROOT}/scripts/rc-ego-001-phase2.sh"

log "=== ingest module unit tests ==="
cd "${STUDIO}"
node --test ingest/ingest.test.mjs

log "=== static: ingest module files ==="
for f in receive-tar.mjs segment-state.mjs tar-validator.mjs session-coordinator.mjs index.mjs; do
  test -f "ingest/${f}"
done

log "=== static: stream-ingest delegates tar upload ==="
rg -q 'ingest/index.mjs' "${STUDIO}/stream-ingest.mjs"

log "Phase 3 acceptance PASSED"
log "Deploy 34: data-lab-platform/scripts/deploy-stream-ingest-v0.0.13-rc.3.sh"
