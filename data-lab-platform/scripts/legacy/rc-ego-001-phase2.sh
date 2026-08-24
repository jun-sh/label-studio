#!/usr/bin/env bash
# Phase 2 acceptance: upload manifest state machine (130 ego-stream-client).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLIENT="${ROOT}/ego-stream-client"

log() { echo "[rc-ego-001-phase2] $*"; }

log "=== Phase 1 regression ==="
"${ROOT}/scripts/rc-ego-001-phase1.sh"

log "=== segment_upload unit tests ==="
cd "${CLIENT}"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_segment_upload.py -v

log "=== static: upload uses manifest state APIs ==="
rg -q "mark_segment_uploading|mark_segment_upload_failed|clear_segment_uploaded" "${CLIENT}/segment_upload.py"

log "Phase 2 acceptance PASSED"
log "Deploy to 130: re-run ego-130-provision.sh; no 34 docker image for this phase."
