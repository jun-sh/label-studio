#!/usr/bin/env bash
# Poll upload process-notify markers and run ego-process (P-Ops-3).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PROCESS_SH="${SCRIPT_DIR}/ego-process"
STATION="${STATION_ID:-ego-001}"
INTERVAL="${EGO_PROCESS_WATCH_INTERVAL_SEC:-30}"

log() { echo "[process-watcher] $*"; }

_run_once() {
  local station="$1"
  local stream="${DATALAB_ROOT}/data-storage/stream/${station}/state"
  local pending="${stream}/process-notify.pending.json"
  local running="${stream}/process-notify.running.json"

  [[ -f "$pending" ]] || return 0
  [[ ! -f "$running" ]] || {
    log "${station}: already running (skip)"
    return 0
  }

  mv "$pending" "$running"
  log "${station}: notify received — starting ego-process"
  set +e
  bash "$PROCESS_SH" "$station" 2>&1
  local rc=$?
  set -e
  rm -f "$running"
  if [[ "$rc" -eq 0 ]]; then
    log "${station}: ego-process OK"
  else
    log "${station}: ego-process failed (exit ${rc})"
    return "$rc"
  fi
}

watch_loop() {
  log "watching ${STATION} every ${INTERVAL}s (Ctrl+C to stop)"
  while true; do
    _run_once "$STATION" || true
    sleep "$INTERVAL"
  done
}

case "${1:-once}" in
  once)
    _run_once "$STATION"
    ;;
  watch|-w)
    watch_loop
    ;;
  -h|--help)
    cat <<EOF
用法: ego-process-watcher.sh [once|watch]

  once   处理一次 pending notify（cron/systemd 适用）
  watch  持续轮询（前台守护）

环境变量:
  STATION_ID                      默认 ego-001
  EGO_PROCESS_WATCH_INTERVAL_SEC  watch 间隔，默认 30
EOF
    ;;
  *)
    echo "未知参数: $1" >&2
    exit 2
    ;;
esac
