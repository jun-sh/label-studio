#!/usr/bin/env bash
# Phase C gray: run N unit-layout capture→upload→derive trips, then acceptance.
#
# Usage:
#   bash rc-ego-001-p2-phase-c-gray.sh              # target 3 episodes total
#   bash rc-ego-001-p2-phase-c-gray.sh --accept-only
#   MIN_TRIPS=5 bash rc-ego-001-p2-phase-c-gray.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
STREAM="${ROOT}/data-storage/stream/${STATION}"
MIN_TRIPS="${MIN_TRIPS:-3}"
LOG="${P2_GRAY_LOG:-${ROOT}/data-lab-platform/.p2-phase-c-gray.log}"
CAPTURE="${ROOT}/data-lab-platform/scripts/rc-ego-001-p0-gray-capture.sh"
ACCEPT="${ROOT}/data-lab-platform/scripts/rc-ego-001-p2-unit-acceptance.sh"

log() { echo "[p2-gray] $*" | tee -a "${LOG}"; }
die() { echo "[p2-gray] ERROR: $*" >&2 | tee -a "${LOG}"; exit 1; }

count_units() {
  find "${STREAM}/derived" -mindepth 1 -maxdepth 1 -name 'sess_*' -type d 2>/dev/null | wc -l
}

if [[ "${1:-}" == "--accept-only" ]]; then
  MIN_EPISODES="${MIN_TRIPS}" bash "${ACCEPT}"
  exit 0
fi

: > "${LOG}"
log "Phase C gray start station=${STATION} MIN_TRIPS=${MIN_TRIPS}"

docker stop data-lab-derive-worker-1 >/dev/null 2>&1 || true

have="$(count_units)"
log "existing derived units=${have}"

while [[ "${have}" -lt "${MIN_TRIPS}" ]]; do
  need=$((MIN_TRIPS - have))
  log "capture trip ($((have + 1))/${MIN_TRIPS}), need ${need} more"
  if ! bash "${CAPTURE}" >>"${LOG}" 2>&1; then
    die "gray capture failed at unit count ${have}"
  fi
  have="$(count_units)"
  log "derived units now=${have}"
done

log "rebuild-view (ensure L2 matches all units)"
DERIVE_LAYOUT=unit STATION_ID="${STATION}" bash "${ROOT}/data-lab-platform/scripts/ego-derive" rebuild-view --station "${STATION}" --json >>"${LOG}" 2>&1

log "fsck"
DERIVE_LAYOUT=unit STATION_ID="${STATION}" bash "${ROOT}/data-lab-platform/scripts/ego-derive" fsck --station "${STATION}" >>"${LOG}" 2>&1

log "acceptance"
MIN_EPISODES="${MIN_TRIPS}" bash "${ACCEPT}" | tee -a "${LOG}"

log "Phase C gray COMPLETE: ${MIN_TRIPS} episodes on unit path"
