#!/usr/bin/env bash
# Persistent convert worker — keeps WiLoR warm and drains pending sessions.
# Usage: ego-convert-worker.sh <station>
set -euo pipefail

STATION="${1:-}"
[[ -n "$STATION" ]] || { echo "usage: ego-convert-worker.sh <station>" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=ego-pipeline-performance.env.sh
source "${SCRIPT_DIR}/ego-pipeline-performance.env.sh"
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PIPELINE_SH="${SCRIPT_DIR}/ego-run-pipeline"
POLL_SEC="${EGO_CONVERT_WORKER_POLL_SEC:-5}"
PIDFILE="${DATALAB_ROOT}/data-storage/pipeline/.convert-worker-${STATION}.pid"
LOG_DIR="${DATALAB_ROOT}/data-storage/logs"
mkdir -p "$LOG_DIR" "${DATALAB_ROOT}/data-storage/pipeline"
RUN_LOG="${LOG_DIR}/ego-convert-worker-${STATION}.log"

log() { echo "[convert-worker $(date +%H:%M:%S)] $*" | tee -a "$RUN_LOG"; }

cleanup() {
  rm -f "$PIDFILE"
}
trap cleanup EXIT INT TERM

echo "$$" > "$PIDFILE"
log "started station=${STATION} pid=$$ poll=${POLL_SEC}s"

while true; do
  mapfile -t PENDING < <(
    python3 "${SCRIPT_DIR}/ego-pipeline-sessions.py" pending "$STATION" \
      --datalab-root "$DATALAB_ROOT" 2>/dev/null || true
  )
  if [[ ${#PENDING[@]} -gt 0 ]]; then
    log "drain pending (${#PENDING[@]}): ${PENDING[*]}"
    EGO_USE_CONVERT_WORKER=0 EGO_AUTO_VIEWER_SYNC=0 \
      bash "$PIPELINE_SH" "$STATION" --skip-deploy --skip-gate --skip-viewer-sync \
      2>&1 | tee -a "$RUN_LOG" || log "batch drain exited non-zero (will retry)"
  fi
  sleep "$POLL_SEC"
done
