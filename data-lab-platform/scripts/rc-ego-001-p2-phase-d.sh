#!/usr/bin/env bash
# Phase D acceptance: legacy incremental derive removed; unit layout is default.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ego-production-defaults.sh
source "${SCRIPT_DIR}/ego-production-defaults.sh"

ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
INGEST="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
STUDIO="${ROOT}/data-lab-platform/lerobot-studio"

PASS=0
FAIL=0

log() { echo "[phase-d] $*"; }
ok() { log "PASS: $*"; PASS=$((PASS + 1)); }
bad() { log "FAIL: $*"; FAIL=$((FAIL + 1)); }

log "=== Phase D acceptance (${STATION}) ==="

for sym in mergeFrameMapIncremental runFourCameraMuxIncremental muxOneCameraIncremental; do
  if grep -rq "${sym}" "${STUDIO}/derive/" --include='*.mjs' 2>/dev/null; then
    bad "legacy symbol still in source: ${sym}"
  else
    ok "removed ${sym}"
  fi
done

if grep -q 'DERIVE_LAYOUT || "unit"' "${STUDIO}/derive/station-context.mjs"; then
  ok "DERIVE_LAYOUT defaults to unit"
else
  bad "DERIVE_LAYOUT default not unit"
fi

if grep -q 'runDerivePipelineUnit' "${STUDIO}/derive/pipeline.mjs" \
  && ! grep -q 'buildFrameMapForDerive' "${STUDIO}/derive/pipeline.mjs"; then
  ok "pipeline.mjs delegates to unit only"
else
  bad "pipeline.mjs still contains legacy path"
fi

log "=== unit tests ==="
if (cd "${STUDIO}" && node --test derive/*.test.mjs); then
  ok "derive/*.test.mjs"
else
  bad "derive/*.test.mjs"
fi

if docker ps --format '{{.Names}}' | grep -q "^${INGEST}$"; then
  img="$(docker inspect "${INGEST}" --format '{{.Config.Image}}')"
  [[ "${img}" == *":${EGO_PRODUCTION_TAG}" ]] && ok "running image ${img}" || log "WARN: image not ${EGO_PRODUCTION_TAG} (${img}) — rebuild with deploy-stream-ingest-v0.1.1-async.sh"

  docker exec "${INGEST}" node -e "
import fs from 'node:fs';
const bad = ['mergeFrameMapIncremental','runFourCameraMuxIncremental','muxOneCameraIncremental'];
for (const p of ['/app/derive/frame-map.mjs','/app/derive/mux-exec.mjs']) {
  const t = fs.readFileSync(p,'utf8');
  for (const s of bad) if (t.includes(s)) process.exit(1);
}
if (!fs.readFileSync('/app/derive/pipeline.mjs','utf8').includes('runDerivePipelineUnit')) process.exit(2);
" >/dev/null 2>&1 && ok "container image Phase D clean" || bad "container still has legacy symbols"
else
  log "SKIP: ${INGEST} not running (deploy v0.1.1 to verify image)"
fi

if [[ -x "${ROOT}/data-lab-platform/scripts/ego-derive" ]]; then
  DERIVE_LAYOUT=unit STATION_ID="${STATION}" bash "${ROOT}/data-lab-platform/scripts/ego-derive" fsck --station "${STATION}" --json \
    >/tmp/phase-d-fsck.json 2>/dev/null \
    && ok "ego-derive fsck" || bad "ego-derive fsck failed"
fi

log "=== summary: pass=${PASS} fail=${FAIL} ==="
[[ "${FAIL}" -eq 0 ]]
