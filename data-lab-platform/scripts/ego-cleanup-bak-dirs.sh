#!/usr/bin/env bash
# Remove Phase B migration *.bak* dirs older than RETENTION_DAYS under stream storage.
#
# Usage:
#   bash ego-cleanup-bak-dirs.sh              # dry-run
#   bash ego-cleanup-bak-dirs.sh --apply      # delete
#   bash ego-cleanup-bak-dirs.sh --apply --station ego-001
#
# Cron example (weekly Sunday 03:15):
#   15 3 * * 0 bash /path/to/data-lab/data-lab-platform/scripts/ego-cleanup-bak-dirs.sh --apply >>/var/log/ego-bak-cleanup.log 2>&1
set -euo pipefail

# NEVER delete these runtime data trees (see .cursor/rules/data-storage-protected.mdc):
#   data-storage/corpus/ data-storage/pipeline/ data-storage/lerobot-qc/
#   data-storage/stream/ data-storage/ego-archive/ data-storage/logs/
# This script only removes *.bak dirs under EGO_STREAM_ROOT (default: data-storage/stream).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
STREAM_ROOT="${EGO_STREAM_ROOT:-${ROOT}/data-storage/stream}"
RETENTION_DAYS="${BAK_RETENTION_DAYS:-7}"
APPLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --station) STATION="$2"; shift 2 ;;
    --station=*) STATION="${1#*=}"; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) STATION="$1"; shift ;;
  esac
done

log() { echo "[bak-cleanup] $*"; }

station_dir="${STREAM_ROOT}/${STATION}"
if [[ ! -d "${station_dir}" ]]; then
  log "station dir missing: ${station_dir}"
  exit 0
fi

now_epoch="$(date +%s)"
cutoff_epoch=$((now_epoch - RETENTION_DAYS * 86400))
removed=0
candidates=0

while IFS= read -r -d '' path; do
  candidates=$((candidates + 1))
  mtime_epoch="$(stat -c '%Y' "${path}" 2>/dev/null || echo 0)"
  age_days=$(( (now_epoch - mtime_epoch) / 86400 ))
  if [[ "${mtime_epoch}" -gt "${cutoff_epoch}" ]]; then
    log "keep (age ${age_days}d): ${path}"
    continue
  fi
  if [[ "${APPLY}" -eq 1 ]]; then
    rm -rf "${path}"
    log "removed (age ${age_days}d): ${path}"
    removed=$((removed + 1))
  else
    log "would remove (age ${age_days}d): ${path}"
    removed=$((removed + 1))
  fi
done < <(find "${station_dir}" -maxdepth 2 \( -name '*.bak' -o -name '*.bak-*' -o -name '*\.bak-*' \) -print0 2>/dev/null)

if [[ "${candidates}" -eq 0 ]]; then
  log "no .bak paths under ${station_dir}"
elif [[ "${APPLY}" -eq 0 ]]; then
  log "dry-run: ${removed} path(s) eligible (>${RETENTION_DAYS}d); use --apply to delete"
else
  log "done: removed ${removed} path(s)"
fi
