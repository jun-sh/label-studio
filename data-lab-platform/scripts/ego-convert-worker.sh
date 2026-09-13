#!/usr/bin/env bash
# Persistent convert worker — keeps WiLoR warm and drains pending sessions.
# Usage: ego-convert-worker.sh <station>
set -euo pipefail

STATION="${1:-}"
[[ -n "$STATION" ]] || { echo "usage: ego-convert-worker.sh <station>" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=ego-pipeline-performance.env.sh
source "${SCRIPT_DIR}/ego-pipeline-performance.env.sh"
# shellcheck source=ego-convert-coord.sh
source "${SCRIPT_DIR}/ego-convert-coord.sh"
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PIPELINE_SH="${SCRIPT_DIR}/ego-run-pipeline"
POLL_SEC="${EGO_CONVERT_WORKER_POLL_SEC:-5}"
PIDFILE="${DATALAB_ROOT}/data-storage/pipeline/.convert-worker-${STATION}.pid"
HEARTBEAT="${DATALAB_ROOT}/data-storage/pipeline/.convert-worker-${STATION}.heartbeat"
LOG_DIR="${DATALAB_ROOT}/data-storage/logs"
mkdir -p "$LOG_DIR" "${DATALAB_ROOT}/data-storage/pipeline"
RUN_LOG="${LOG_DIR}/ego-convert-worker-${STATION}.log"

log() { echo "[convert-worker $(date +%H:%M:%S)] $*" | tee -a "$RUN_LOG"; }

_touch_heartbeat() {
  date -Iseconds > "$HEARTBEAT"
}

_reclaim_orphan_workers() {
  local opid
  while read -r opid; do
    [[ -z "$opid" || "$opid" == "$$" ]] && continue
    if kill -0 "$opid" 2>/dev/null; then
      log "stopping orphan convert worker pid=${opid}"
      kill -TERM "$opid" 2>/dev/null || true
    fi
  done < <(pgrep -f "ego-convert-worker\\.sh[[:space:]]+${STATION}(\\s|$)" 2>/dev/null || true)
}

cleanup() {
  rm -f "$PIDFILE"
}
trap cleanup EXIT INT TERM

if [[ -f "$PIDFILE" ]]; then
  oldpid="$(tr -d '[:space:]' < "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "$oldpid" && "$oldpid" != "$$" ]] && kill -0 "$oldpid" 2>/dev/null; then
    log "convert worker already active (pid=${oldpid}); exiting"
    exit 0
  fi
fi
_reclaim_orphan_workers
sleep 1
_reclaim_orphan_workers

echo "$$" > "$PIDFILE"
_touch_heartbeat
log "started station=${STATION} pid=$$ poll=${POLL_SEC}s"

while true; do
  _touch_heartbeat
  mapfile -t PENDING < <(
    python3 "${SCRIPT_DIR}/ego-pipeline-sessions.py" pending "$STATION" \
      --datalab-root "$DATALAB_ROOT" 2>/dev/null || true
  )
  if [[ ${#PENDING[@]} -gt 0 ]]; then
    if ego_convert_coord_inline_active "$STATION" "$DATALAB_ROOT"; then
      log "skip drain — inline ego-process/L2 convert active for ${STATION}"
      sleep "$POLL_SEC"
      continue
    fi
    log "drain pending (${#PENDING[@]}): ${PENDING[*]}"
    EGO_USE_CONVERT_WORKER=0 EGO_AUTO_VIEWER_SYNC=0 \
      bash "$PIPELINE_SH" "$STATION" --skip-deploy --skip-gate --skip-viewer-sync \
      2>&1 | tee -a "$RUN_LOG" || log "batch drain exited non-zero (will retry)"
    _touch_heartbeat
  fi
  sleep "$POLL_SEC"
done
