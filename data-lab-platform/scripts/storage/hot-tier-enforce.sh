#!/usr/bin/env bash
# Session-level cold migration when hot tier exceeds quota (no full-tree scan).
set -euo pipefail

ENV_FILE="${STREAM_STORAGE_ENV:-/opt/datalab/env/stream-storage.env}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

STATION_ID="${STATION_ID:-ego-001}"
STATION_ROOT="${STATION_ROOT:?set STATION_ROOT in $ENV_FILE}"
COLD_ROOT="${COLD_ROOT:?set COLD_ROOT in $ENV_FILE}"
ARCHIVE="${STATION_ROOT}/archive"
LOG="${HOT_TIER_LOG:-/opt/datalab/log/hot-tier-enforce.log}"
QUOTA_GB="${STREAM_QUOTA_GB:-20}"
HIGH_PCT="${HOT_HIGH_WATER_PERCENT:-90}"
MAX_MOVE="${COLD_MIGRATE_MAX_PER_RUN:-3}"

mkdir -p "$(dirname "$LOG")" "$COLD_ROOT"
if [ ! -d "$ARCHIVE" ] || [ ! -r "$ARCHIVE" ]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  exec "$SCRIPT_DIR/migrate-archive-docker.sh" enforce
fi
mkdir -p "$ARCHIVE"
exec >>"$LOG" 2>&1

usage_bytes() {
  local hk="${STATION_ROOT}/live/disk-housekeeping.json"
  if [ -f "$hk" ]; then
    python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(int(d.get('usageBytes') or 0))" "$hk" 2>/dev/null && return 0
  fi
  du -sb "$STATION_ROOT" 2>/dev/null | awk '{print $1}'
}

quota_bytes=$((QUOTA_GB * 1024 * 1024 * 1024))
threshold=$((quota_bytes * HIGH_PCT / 100))
usage="$(usage_bytes || echo 0)"

echo "=== $(date -Is) hot-tier-enforce station=${STATION_ID} usage=${usage} threshold=${threshold} ==="

if [ ! -d "$ARCHIVE" ]; then
  echo "no archive dir"
  exit 0
fi

if [ "$usage" -le "$threshold" ]; then
  echo "OK under high-water"
  exit 0
fi

moved=0
shopt -s nullglob
while IFS= read -r dir; do
  [ -d "$dir" ] || continue
  [ "$moved" -lt "$MAX_MOVE" ] || break
  name=$(basename "$dir")
  out="${COLD_ROOT}/${name}.tar.gz"
  if [ -f "$out" ]; then
    echo "remove duplicate hot archive (cold exists): $name"
    rm -rf "$dir"
    moved=$((moved + 1))
    continue
  fi
  echo "quota migrate $dir -> $out"
  tar -czf "$out" -C "$ARCHIVE" "$name"
  rm -rf "$dir"
  moved=$((moved + 1))
  usage="$(usage_bytes || echo 0)"
  [ "$usage" -le "$threshold" ] && break
done < <(find "$ARCHIVE" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -n | cut -d' ' -f2-)

echo "moved=${moved} usage_after=$(usage_bytes || echo 0)"
echo "=== done ==="
