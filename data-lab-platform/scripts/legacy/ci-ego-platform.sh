#!/usr/bin/env bash
# CI / local gate for ego-001 platform (no 130 SSH required).
# Usage:
#   RC_STATION=ego-001 bash data-lab-platform/scripts/ci-ego-platform.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${RC_STATION:-ego-001}"
CID="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
STREAM_HOST="${ROOT}/data-storage/stream/${STATION}"

log() { echo "[ci-ego] $*"; }
die() { echo "[ci-ego] FAIL: $*" >&2; exit 1; }

if ! docker ps --format '{{.Names}}' | grep -qx "${CID}"; then
  die "container ${CID} not running — start platform stack first"
fi

log "=== rc-acceptance (${STATION}) ==="
RC_STATION="${STATION}" bash "${ROOT}/data-lab-platform/scripts/rc-acceptance.sh"

if [[ -f "${STREAM_HOST}/meta/info.json" ]]; then
  log "=== rc-e2e derive READY (skip upload) ==="
  RC_STATION="${STATION}" RC_SKIP_UPLOAD=1 RC_ASSERT_READY=1 \
    bash "${ROOT}/data-lab-platform/scripts/rc-e2e-upload.sh"
else
  log "skip derive READY (no stream data — capture + upload or run ego-001-plan-b-e2e.sh)"
fi

log "=== derive-worker image-only check ==="
MJS_MOUNTS="$(docker inspect data-lab-derive-worker-1 --format '{{range .Mounts}}{{println .Source}}{{end}}' 2>/dev/null \
  | grep -c 'lerobot-studio/.*\.mjs$' || true)"
if [[ "${MJS_MOUNTS}" -gt 0 ]]; then
  die "derive-worker has ${MJS_MOUNTS} host .mjs bind-mount(s); use production compose overlay only"
fi
log "derive-worker mounts ok (no .mjs bind-mount)"

log "=== rc-high-freq-imu ==="
bash "${ROOT}/data-lab-platform/scripts/rc-high-freq-imu.sh"

log "=== CI ego-platform PASSED ==="
