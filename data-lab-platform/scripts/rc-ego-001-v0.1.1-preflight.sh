#!/usr/bin/env bash
# v0.1.1 production preflight: Phase D unit-only, P-Ops endpoints, async-ready.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ego-production-defaults.sh
source "${SCRIPT_DIR}/ego-production-defaults.sh"

ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
INGEST="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
HOST="${LABEL_STUDIO_HOST:-http://127.0.0.1:8080}"
HOST="${HOST%/}"
TOKEN="${STATION_UPLOAD_TOKEN:-dl-upload-ego-001-v1}"

PASS=0
FAIL=0
WARN=0

log() { echo "[v0.1.1-preflight] $*"; }
ok() { log "PASS: $*"; PASS=$((PASS + 1)); }
bad() { log "FAIL: $*"; FAIL=$((FAIL + 1)); }
warn() { log "WARN: $*"; WARN=$((WARN + 1)); }

log "=== v0.1.1 preflight (${STATION}) ==="

[[ -f "${ROOT}/${EGO_COMPOSE_OVERLAY}" ]] \
  && ok "overlay ${EGO_COMPOSE_OVERLAY}" || bad "missing ${EGO_COMPOSE_OVERLAY}"
[[ -f "${ROOT}/${EGO_COMPOSE_OVERLAY_ASYNC}" ]] \
  && ok "async overlay ${EGO_COMPOSE_OVERLAY_ASYNC}" || bad "missing async overlay"

if docker ps --format '{{.Names}}' | grep -q "^${INGEST}$"; then
  img="$(docker inspect "${INGEST}" --format '{{.Config.Image}}')"
  [[ "${img}" == *":${EGO_PRODUCTION_TAG}" ]] && ok "image ${img}" || bad "image not ${EGO_PRODUCTION_TAG}: ${img}"

  dl="$(docker exec "${INGEST}" printenv DERIVE_LAYOUT 2>/dev/null || true)"
  if [[ -z "${dl}" || "${dl}" == "unit" ]]; then
    ok "DERIVE_LAYOUT=${dl:-unit (code default)}"
  else
    bad "DERIVE_LAYOUT=${dl}"
  fi

  se="$(docker exec "${INGEST}" printenv STREAM_SESSION_SINGLE_EPISODE 2>/dev/null || echo "?")"
  [[ "${se}" == "0" ]] && ok "STREAM_SESSION_SINGLE_EPISODE=0" || warn "STREAM_SESSION_SINGLE_EPISODE=${se}"

  docker exec "${INGEST}" node -e "
import fs from 'node:fs';
const checks = [
  ['/app/ingest-server.mjs', ['process-notify']],
  ['/app/derive-async.mjs', ['handleProcessNotify']],
  ['/app/derive/pipeline.mjs', ['runDerivePipelineUnit']],
  ['/app/derive/frame-map.mjs', [], ['mergeFrameMapIncremental']],
  ['/app/derive/mux-exec.mjs', [], ['runFourCameraMuxIncremental']],
];
for (const [p, must = [], mustNot = []] of checks) {
  const t = fs.readFileSync(p, 'utf8');
  for (const n of must) if (!t.includes(n)) process.exit(1);
  for (const n of mustNot) if (t.includes(n)) process.exit(2);
}
" >/dev/null 2>&1 && ok "Phase D image + process-notify" || bad "image content check failed"
else
  bad "container ${INGEST} not running"
fi

[[ -x "${ROOT}/data-lab-platform/scripts/ego-process" ]] && ok "ego-process" || bad "ego-process missing"
[[ -x "${ROOT}/data-lab-platform/scripts/ego-upload-station.sh" ]] && ok "ego-upload-station" || bad "ego-upload missing"
[[ -f "${ROOT}/data-lab-platform/gateway/nginx/snippets/datalab-stream-ingest.conf" ]] \
  && grep -q process-notify "${ROOT}/data-lab-platform/gateway/nginx/snippets/datalab-stream-ingest.conf" \
  && ok "nginx process-notify route" || bad "nginx process-notify route missing"

if curl -sf -X POST "${HOST}/lerobot/api/collection/stations/${STATION}/process-notify" \
  -H "X-Station-Token: ${TOKEN}" -H "Content-Type: application/json" -d '{}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('queued') else 1)" 2>/dev/null; then
  ok "process-notify API"
  rm -f "${ROOT}/data-storage/stream/${STATION}/state/process-notify.pending.json" 2>/dev/null || true
else
  bad "process-notify API unreachable"
fi

log "=== summary: pass=${PASS} fail=${FAIL} warn=${WARN} ==="
[[ "${FAIL}" -eq 0 ]]
