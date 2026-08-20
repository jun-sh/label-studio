#!/usr/bin/env bash
# Wait until all derive-pending sessions reach session.READY (async worker or manual derive).
set -euo pipefail

STATION="${STATION_ID:?STATION_ID required}"
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SESSIONS_PY="${SCRIPT_DIR}/ego-pipeline-sessions.py"

TIMEOUT="${DERIVE_TIMEOUT_SEC:-3600}"
INTERVAL="${DERIVE_WAIT_INTERVAL_SEC:-10}"
PLATFORM_HOST="${LABEL_STUDIO_HOST:-http://127.0.0.1:8080}"
PLATFORM_HOST="${PLATFORM_HOST%/}"

log() { echo "[wait-derive] $*"; }
die() { echo "[wait-derive] ERROR: $*" >&2; exit 1; }

_py() {
  python3 "$SESSIONS_PY" "$@" --datalab-root "$DATALAB_ROOT"
}

_check_failed_sessions() {
  local stream="${DATALAB_ROOT}/data-storage/stream/${STATION}/state/sessions"
  local failed=()
  for marker in "${stream}"/sess_*/session.FAILED; do
    [[ -f "$marker" ]] || continue
    local sid
    sid="$(basename "$(dirname "$marker")")"
    local pending=0
    if ! [[ -f "${stream}/${sid}/session.READY" ]]; then
      pending=1
    fi
    if [[ "$pending" -eq 1 ]]; then
      failed+=("$sid")
    fi
  done
  if [[ ${#failed[@]} -gt 0 ]]; then
    die "session.FAILED (未 READY): ${failed[*]} — 查 ego-derive status / derive-worker 日志"
  fi
}

deadline=$((SECONDS + TIMEOUT))
last_line=""

while (( SECONDS < deadline )); do
  mapfile -t PENDING < <(_py derive-pending "$STATION" 2>/dev/null || true)
  _check_failed_sessions

  if [[ ${#PENDING[@]} -eq 0 ]]; then
    log "${STATION} 全部 session 已 READY"
    exit 0
  fi

  status_json="$(curl -sf "${PLATFORM_HOST}/lerobot/api/collection/stations/${STATION}/derive-status" 2>/dev/null || echo '{}')"
  line="$(date +%H:%M:%S) pending=${#PENDING[@]} [${PENDING[*]}] $(python3 -c "
import json, sys
d = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
p = d.get('progress') or {}
print(f\"phase={d.get('phase','?')} markers={p.get('markers',0)}/{p.get('total',0)} parquet={p.get('parquetRows',0)}\")
" "$status_json" 2>/dev/null || echo "derive-status unavailable")"

  if [[ "$line" != "$last_line" ]]; then
    log "$line"
    last_line="$line"
  fi

  sleep "$INTERVAL"
done

die "等待 derive READY 超时 (${TIMEOUT}s)，仍 pending: ${PENDING[*]}"
