#!/usr/bin/env bash
# Phase 5 acceptance: READY gate + lifecycle GC + session markers.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STUDIO="${ROOT}/lerobot-studio"

log() { echo "[rc-ego-001-phase5] $*"; }

log "=== Phase 3 + Phase 4 regression ==="
"${ROOT}/scripts/rc-ego-001-phase3.sh"
cd "${STUDIO}"
node --test derive/derive.test.mjs

log "=== Phase5 unit tests ==="
cd "${STUDIO}"
node --test derive/phase5.test.mjs

log "=== static: Phase5 module files ==="
test -f derive/ready-gate.mjs
test -f derive/lifecycle-gc.mjs
rg -q 'runReadyGate' derive/pipeline.mjs
rg -q 'runLifecycleGc' derive/pipeline.mjs
rg -q 'ready_gate' session-markers.mjs

log "Phase 5 acceptance PASSED"
log "Deploy 34: data-lab-platform/scripts/deploy-stream-ingest-v0.0.13-rc.5.sh"
