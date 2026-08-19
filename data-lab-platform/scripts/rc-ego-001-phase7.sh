#!/usr/bin/env bash
# Phase 7 acceptance: tech debt purge + CI adaptation + T1-T8 test matrix + v0.0.13 gate.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "${ROOT}/.." && pwd)"
STUDIO="${ROOT}/lerobot-studio"
SCRIPTS="${ROOT}/scripts"
PLATFORM_ROOT="${EGO_PLATFORM_ROOT:-$(dirname "${REPO}")/ego-platform}"
STATION="${RC_STATION:-ego-001}"

log() { echo "[rc-ego-001-phase7] $*"; }
fail() { echo "[rc-ego-001-phase7] FAIL: $*" >&2; exit 1; }

log "=== T8: tech debt grep scan (exclude legacy/ + historical docs) ==="
scan_debt() {
  local pattern="$1"
  local extra_glob="${2:-}"
  local hits
  local -a globs=(
    --glob '!**/legacy/**'
    --glob '!**/docs/**'
    --glob '!**/RELEASE*.md'
    --glob '!**/_deprecated/**'
    --glob '!**/ego-local-web/**'
    --glob '!**/test-d*.mjs'
    --glob '!**/test-d*.py'
    --glob '!**/rc-ego-001-phase*.sh'
    --glob '!**/rc-acceptance.sh'
    --glob '!**/deploy-stream-ingest-v0.0.1*.sh'
    --glob '!**/deploy-stream-ingest-v0.0.9*.sh'
    --glob '!**/docker-compose.v0.0.*.yml'
    --glob '!**/Stream-Optimization*.md'
    --glob '!**/deploy-stream-ingest-v0.0.13.sh'
  )
  if [[ -n "${extra_glob}" ]]; then
    globs+=(--glob "${extra_glob}")
  fi
  hits="$(rg -n "${pattern}" \
    "${ROOT}/lerobot-studio" \
    "${ROOT}/scripts" \
    "${ROOT}/ego-stream-client" \
    "${ROOT}/client" \
    "${ROOT}/config" \
    "${PLATFORM_ROOT}/src" \
    "${REPO}/label_studio/templates/datalab" \
    "${globs[@]}" \
    2>/dev/null || true)"
  if [[ -n "${hits}" ]]; then
    echo "${hits}"
    return 1
  fi
  return 0
}

scan_debt 'shouldDeferStagingPurge' || fail "shouldDeferStagingPurge still referenced"
scan_debt 'frameBase' || fail "frameBase still referenced"
scan_debt 'ego-lan-214' || fail "ego-lan-214 still referenced"
scan_debt 'high_freq' || fail "high_freq still referenced"
scan_debt 'ingest-imu-high-freq' || fail "ingest-imu-high-freq still referenced"
scan_debt 'segment_mp4' || fail "segment_mp4 still referenced"
scan_debt 'DERIVE_MCAP_EXPORT' || fail "DERIVE_MCAP_EXPORT still referenced"
scan_debt 'dev-mjs-bind' '!**/ci-ego-platform.sh' || fail "bind-mount dev override still referenced"
test ! -f "${ROOT}/docker-compose.dev-mjs-bind.yml"
test ! -f "${STUDIO}/scripts/ingest-imu-high-freq.py"
test ! -f "${SCRIPTS}/rc-high-freq-imu.sh"
log "T8 PASSED"

log "=== static: §13 deletion checklist ==="
test ! -f "${SCRIPTS}/legacy/ego-lan-214-reset-34-only.sh"
! rg -q 'ego-lan-214' "${STUDIO}/config/station-topology.json"
rg -q 'evaluateStagingGc' "${STUDIO}/stream-ingest.mjs"
! rg -q 'mcapExportEnabled' "${STUDIO}/stream-ingest.mjs"
rg -q 'sensor_raw/imu' "${PLATFORM_ROOT}/src/ego_platform/lerobot/sensor_raw_imu.py"

log "=== T4: IMU dual storage unit tests ==="
cd "${STUDIO}"
node --test derive/derive.test.mjs derive/phase5.test.mjs

log "=== T3: multi-session frame_map continuity ==="
node --test derive/derive.test.mjs

log "=== T6: ready-gate failure reason codes ==="
node -e "
import { runReadyGate } from './derive/ready-gate.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
const root = fs.mkdtempSync(path.join(os.tmpdir(), 't6-'));
fs.mkdirSync(path.join(root,'meta/episodes'),{recursive:true});
fs.writeFileSync(path.join(root,'meta/episodes/episode_000.json'), JSON.stringify({length:0,frame_segments:[]}));
const r = runReadyGate('ego-001', root, 'sess_x');
if (r.ok || !r.reason?.code) process.exit(1);
console.log('T6 ok reason.code=' + r.reason.code);
"

log "=== T2: reset preserves raw archives ==="
TMP="$(mktemp -d)"
ST="${TMP}/${STATION}"
mkdir -p "${ST}/raw/segments" "${ST}/data/chunk-000"
echo fake > "${ST}/raw/segments/seg_000001.tar.zst"
echo x > "${ST}/data/chunk-000/file-000.parquet"
# shellcheck source=ego-reset-34-wipe-stream.sh
source "${SCRIPTS}/ego-reset-34-wipe-stream.sh"
ego_reset_wipe_stream_derived "${ST}" "${STATION}"
test -f "${ST}/raw/segments/seg_000001.tar.zst"
test ! -d "${ST}/data"
rm -rf "${TMP}"
log "T2 PASSED"

log "=== T5/T1 smoke: ingest + derive modules import ==="
docker run --rm --entrypoint node \
  -v "${STUDIO}:/app:ro" \
  data-lab-lerobot-studio:v0.0.13-rc.6 \
  -e "import('/app/derive/index.mjs'); import('/app/ingest/index.mjs'); console.log('modules ok');" \
  2>/dev/null || node -e "import('${STUDIO}/derive/index.mjs'); import('${STUDIO}/ingest/index.mjs'); console.log('modules ok');"

log "=== CI: rc-acceptance (JPEG + derive async off) ==="
"${SCRIPTS}/rc-acceptance.sh"

log "=== Phase 1-6 full regression ==="
"${SCRIPTS}/rc-ego-001-phase6.sh"

log "=== T7: CLI signatures unchanged ==="
"${SCRIPTS}/ego-deliver" --help >/dev/null
"${SCRIPTS}/ego-derive" --help >/dev/null
"${SCRIPTS}/ego-run-pipeline" --help >/dev/null

log "Phase 7 acceptance PASSED"
log "Deploy production: data-lab-platform/scripts/deploy-stream-ingest-v0.0.13.sh"
