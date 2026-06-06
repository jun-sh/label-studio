#!/usr/bin/env bash
# Enforce combined hot+cold quota under data-lab/data-storage (default 512GB).
# Session-level cold migration remains in hot-tier-enforce.sh / archive-to-cold.sh.
set -euo pipefail

ENV_FILE="${STREAM_STORAGE_ENV:-}"
if [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
STORAGE_ROOT="${DATALAB_STORAGE_ROOT:-${REPO_ROOT}/data-storage}"
HOT_ROOT="${STORAGE_ROOT}/stream"
COLD_ROOT="${STORAGE_ROOT}/ego-archive"
QUOTA_GB="${DATALAB_STORAGE_QUOTA_GB:-512}"
HIGH_WATER_GB="${DATALAB_STORAGE_HIGH_WATER_GB:-480}"
COLD_RETENTION_DAYS="${COLD_RETENTION_DAYS:-7}"
STAGING_MAX_AGE_MIN="${STAGING_PURGE_MINUTES:-120}"
LOG="${STORAGE_QUOTA_LOG:-/opt/datalab/log/storage-quota-enforce.log}"

mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

quota_bytes=$((QUOTA_GB * 1024 * 1024 * 1024))
high_bytes=$((HIGH_WATER_GB * 1024 * 1024 * 1024))

usage_bytes() {
  local hot=0 cold=0
  [ -d "$HOT_ROOT" ] && hot=$(du -sb "$HOT_ROOT" 2>/dev/null | awk '{print $1}')
  [ -d "$COLD_ROOT" ] && cold=$(du -sb "$COLD_ROOT" 2>/dev/null | awk '{print $1}')
  echo $((hot + cold))
}

usage="$(usage_bytes)"
echo "=== $(date -Is) storage-quota usage=${usage} high=${high_bytes} quota=${quota_bytes} ==="

if [ "$usage" -le "$high_bytes" ]; then
  echo "OK under high-water"
  exit 0
fi

# 1) Drop old cold tarballs
if [ -d "$COLD_ROOT" ]; then
  find "$COLD_ROOT" -type f -name '*.tar.gz' -mtime +"${COLD_RETENTION_DAYS}" -print -delete || true
fi

usage="$(usage_bytes)"
if [ "$usage" -le "$high_bytes" ]; then
  echo "OK after cold retention purge"
  exit 0
fi

# 2) Purge stale staging JPGs on all stations (mux should have consumed them)
if [ -d "$HOT_ROOT" ]; then
  find "$HOT_ROOT" -path '*/_staging/*' -name 'frame_*.jpg' -mmin +"${STAGING_MAX_AGE_MIN}" -print -delete 2>/dev/null || true
fi

usage="$(usage_bytes)"
if [ "$usage" -le "$high_bytes" ]; then
  echo "OK after staging purge"
  exit 0
fi

# 3) Oldest cold archives by mtime
if [ -d "$COLD_ROOT" ]; then
  while [ "$usage" -gt "$high_bytes" ]; do
    oldest=$(find "$COLD_ROOT" -type f -name '*.tar.gz' -printf '%T+ %p\n' 2>/dev/null | sort | head -1 | cut -d' ' -f2-)
    [ -n "$oldest" ] || break
    echo "delete cold: $oldest"
    rm -f "$oldest"
    usage="$(usage_bytes)"
  done
fi

echo "final usage=${usage}"
