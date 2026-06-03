#!/usr/bin/env bash
# EGO 214 collector disk / cache alert. No capture code changes.
set -euo pipefail

CACHE_ROOT="${EGO_LOCAL_CACHE_ROOT:-/home/server/cache/ego-lan-214}"
LOG="${STORAGE_ALERT_LOG:-${CACHE_ROOT}/logs/storage-alert.log}"
DISK_WARN="${DISK_WARN_PERCENT:-85}"
DISK_CRIT="${DISK_CRIT_PERCENT:-92}"
CACHE_MAX_GB="${CACHE_MAX_GB:-8}"
UPLOAD_URL="${UPLOAD_URL:-http://10.10.10.34:8080/lerobot/api/collection/stations/ego-lan-214/upload}"

mkdir -p "$(dirname "$LOG")" "${CACHE_ROOT}/logs"
exec >>"$LOG" 2>&1

log() { echo "$(date -Is) $*"; }

root_pct() { df -P / | awk 'NR==2 {gsub(/%/,"",$5); print $5}'; }
cache_bytes() { du -sb "$CACHE_ROOT" 2>/dev/null | awk '{print $1}'; }

log "=== 214 check start ==="
pct="$(root_pct)"
if [ -n "$pct" ]; then
  if [ "$pct" -ge "$DISK_CRIT" ]; then log "[CRIT] root disk ${pct}%"; 
  elif [ "$pct" -ge "$DISK_WARN" ]; then log "[WARN] root disk ${pct}%";
  else log "OK root disk ${pct}%"; fi
fi

cb="$(cache_bytes)"
max_b=$((CACHE_MAX_GB * 1024 * 1024 * 1024))
if [ -n "$cb" ] && [ "$cb" -gt "$max_b" ]; then
  log "[WARN] cache ${cb} bytes > cap ${max_b}"
else
  log "OK cache bytes=${cb:-0}"
fi

if systemctl --user is-active --quiet ecs-record-oak-stream 2>/dev/null; then
  log "OK ecs-record-oak-stream active"
else
  log "[WARN] ecs-record-oak-stream not active"
fi

code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 5 "${UPLOAD_URL}" 2>/dev/null || echo 000)"
if [ "$code" = "200" ] || [ "$code" = "405" ]; then
  log "OK upload endpoint HTTP ${code}"
else
  log "[WARN] upload endpoint HTTP ${code}"
fi

log "=== 214 check end ==="
