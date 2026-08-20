#!/usr/bin/env bash
# P-Ops-2 preflight: async derive-worker + DERIVE_ASYNC=1 + unit layout.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
INGEST="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
WORKER="${DERIVE_WORKER_CONTAINER:-data-lab-derive-worker-1}"
HOST="${LABEL_STUDIO_HOST:-http://127.0.0.1:8080}"
HOST="${HOST%/}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ego-production-defaults.sh
source "${SCRIPT_DIR}/ego-production-defaults.sh"

PASS=0
FAIL=0
WARN=0

log() { echo "[ops-async-preflight] $*"; }
ok() { log "PASS: $*"; PASS=$((PASS + 1)); }
bad() { log "FAIL: $*"; FAIL=$((FAIL + 1)); }
warn() { log "WARN: $*"; WARN=$((WARN + 1)); }

log "=== P-Ops-2 async preflight (${STATION}) ==="

if docker ps --format '{{.Names}}' | grep -q "^${INGEST}$"; then
  da="$(docker exec "${INGEST}" printenv DERIVE_ASYNC 2>/dev/null || echo "?")"
  da1="$(docker exec "${INGEST}" printenv DERIVE_ASYNC_EGO_001 2>/dev/null || echo "?")"
  [[ "${da}" == "1" ]] && ok "DERIVE_ASYNC=1" || bad "DERIVE_ASYNC=${da} (expect 1 for P-Ops-2)"
  [[ "${da1}" == "1" || "${da1}" == "?" ]] && ok "DERIVE_ASYNC_EGO_001=${da1}" || bad "DERIVE_ASYNC_EGO_001=${da1}"
  dl="$(docker exec "${INGEST}" printenv DERIVE_LAYOUT 2>/dev/null || true)"
  if [[ -z "${dl}" || "${dl}" == "unit" ]]; then
    ok "DERIVE_LAYOUT=${dl:-unit (code default)}"
  else
    warn "DERIVE_LAYOUT=${dl}"
  fi
else
  bad "container ${INGEST} not running"
fi

if docker ps --format '{{.Names}}' | grep -q "^${WORKER}$"; then
  ok "derive-worker running"
else
  bad "derive-worker not running (P-Ops-2 requires async worker)"
fi

[[ -f "${ROOT}/${EGO_COMPOSE_OVERLAY_ASYNC}" ]] \
  && ok "async overlay present (${EGO_COMPOSE_OVERLAY_ASYNC})" || bad "missing async overlay"

[[ -x "${ROOT}/data-lab-platform/scripts/ego-process" ]] \
  && ok "ego-process installed" || bad "ego-process missing"

mapfile -t PENDING < <(python3 "${ROOT}/data-lab-platform/scripts/ego-pipeline-sessions.py" \
  derive-pending "${STATION}" --datalab-root "${ROOT}" 2>/dev/null || true)
if [[ ${#PENDING[@]} -eq 0 ]]; then
  ok "no derive-pending sessions"
else
  warn "derive-pending: ${PENDING[*]}"
fi

if curl -sf --max-time 15 "${HOST}/lerobot/api/collection/stations/${STATION}/derive-status" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
async_on = d.get('asyncEnabled')
print('asyncEnabled', async_on, 'phase', d.get('phase'))
sys.exit(0 if async_on else 1)
" >/dev/null 2>&1; then
  ok "derive-status asyncEnabled=true"
else
  warn "derive-status asyncEnabled not true (may be manual mode)"
fi

log "=== summary: pass=${PASS} fail=${FAIL} warn=${WARN} ==="
[[ "${FAIL}" -eq 0 ]]
