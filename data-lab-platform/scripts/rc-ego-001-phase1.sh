#!/usr/bin/env bash
# Phase 1 acceptance: segment_store manifest v2 + GC rules (130 ego-stream-client).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLIENT="${ROOT}/ego-stream-client"

log() { echo "[rc-ego-001-phase1] $*"; }

log "=== segment_store unit tests ==="
cd "${CLIENT}"
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/test_segment_store.py -v

log "=== static: no auto-purge symbols in segment_store ==="
if rg -n "SEGMENT_AUTO_PURGE_PENDING|purge_oldest_pending_segments" "${CLIENT}/segment_store.py"; then
  echo "FAIL: legacy auto-purge still present" >&2
  exit 1
fi

log "=== static: manifest v2 marker in source ==="
rg -q "MANIFEST_SCHEMA_VERSION = 2" "${CLIENT}/segment_store.py"

log "Phase 1 acceptance PASSED"
log "Deploy to 130: re-run ego-130-provision.sh (edge venv); no 34 docker image for this phase."
