#!/usr/bin/env bash
# Data Lab storage / ingest alert (host 34). No core service code changes.
set -euo pipefail

STATION_ID="${STATION_ID:-ego-lan-214}"
LOG="${STORAGE_ALERT_LOG:-/opt/datalab/log/storage-alert.log}"
STATE="${STORAGE_ALERT_STATE:-/opt/datalab/log/storage-alert.state}"
DISK_WARN="${DISK_WARN_PERCENT:-85}"
DISK_CRIT="${DISK_CRIT_PERCENT:-92}"
STREAM_WARN="${STREAM_DISK_WARN_PERCENT:-80}"
STREAM_CRIT="${STREAM_DISK_CRIT_PERCENT:-95}"
MIN_UPLOAD_FPS_WARN="${MIN_UPLOAD_FPS_WARN:-3}"
PUBLIC_BASE="${PUBLIC_BASE:-http://127.0.0.1:8080}"

mkdir -p "$(dirname "$LOG")" "$(dirname "$STATE")"
exec >>"$LOG" 2>&1

now_iso() { date -Is; }
log() { echo "$(now_iso) $*"; }

root_disk_pct() {
  df -P / | awk 'NR==2 {gsub(/%/,"",$5); print $5}'
}

stream_status_json() {
  timeout 20 docker exec data-lab-lerobot-1 wget -qO- --timeout=18 \
    "http://127.0.0.1:7860/lerobot/api/stream/${STATION_ID}/status" 2>/dev/null || echo "{}"
}

read_state() {
  [ -f "$STATE" ] && cat "$STATE" || echo "ingest_frames=0 ts=0"
}

write_state() {
  printf 'ingest_frames=%s ts=%s\n' "$1" "$2" >"$STATE"
}

alert() {
  local level="$1"
  shift
  log "[$level] $*"
}

log "=== check start station=${STATION_ID} ==="

host_pct="$(root_disk_pct)"
if [ -n "$host_pct" ]; then
  if [ "$host_pct" -ge "$DISK_CRIT" ]; then
    alert CRIT "34 root disk ${host_pct}% >= ${DISK_CRIT}%"
  elif [ "$host_pct" -ge "$DISK_WARN" ]; then
    alert WARN "34 root disk ${host_pct}% >= ${DISK_WARN}%"
  else
    log "OK 34 root disk ${host_pct}%"
  fi
fi

json="$(stream_status_json)"
if [ "$json" = "{}" ] || [ -z "$json" ]; then
  alert WARN "stream status API unavailable"
else
  usage_pct="$(echo "$json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('diskUsagePercent',0))" 2>/dev/null || echo 0)"
  ingest_frames="$(echo "$json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(int(d.get('ingestFrames') or 0))" 2>/dev/null || echo 0)"
  session_id="$(echo "$json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sessionId') or '')" 2>/dev/null || echo "")"
  log "stream diskUsagePercent=${usage_pct}% ingestFrames=${ingest_frames} session=${session_id:-none}"
  if python3 -c "exit(0 if float('${usage_pct}') >= ${STREAM_CRIT} else 1)" 2>/dev/null; then
    alert CRIT "stream quota usage ${usage_pct}% >= ${STREAM_CRIT}%"
  elif python3 -c "exit(0 if float('${usage_pct}') >= ${STREAM_WARN} else 1)" 2>/dev/null; then
    alert WARN "stream quota usage ${usage_pct}% >= ${STREAM_WARN}%"
  fi
  prev="$(read_state)"
  prev_frames="$(echo "$prev" | sed -n 's/ingest_frames=\([0-9]*\).*/\1/p')"
  prev_ts="$(echo "$prev" | sed -n 's/.*ts=\([0-9]*\).*/\1/p')"
  now_ts="$(date +%s)"
  if [ -n "$prev_ts" ] && [ "$prev_ts" -gt 0 ] && [ -n "$session_id" ]; then
    dt=$((now_ts - prev_ts))
    if [ "$dt" -gt 0 ]; then
      delta=$((ingest_frames - prev_frames))
      fps="$(python3 -c "print(round(${delta}/${dt},2))")"
      log "upload_rate ~${fps} fps (delta=${delta} over ${dt}s)"
      if python3 -c "exit(0 if float('${fps}') < ${MIN_UPLOAD_FPS_WARN} else 1)" 2>/dev/null; then
        alert WARN "upload rate ${fps} fps < ${MIN_UPLOAD_FPS_WARN} (station may be stalled)"
      fi
    fi
  fi
  write_state "$ingest_frames" "$now_ts"
fi

ping_json="$(curl -fsS --max-time 5 "${PUBLIC_BASE}/lerobot/api/collection/stations/${STATION_ID}/ping" 2>/dev/null || echo "")"
if [ -z "$ping_json" ]; then
  alert WARN "station ping failed via ${PUBLIC_BASE}"
else
  online="$(echo "$ping_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print('1' if d.get('online') else '0')" 2>/dev/null || echo 0)"
  [ "$online" = "1" ] && log "OK station online" || alert WARN "station offline"
fi

log "=== check end ==="
