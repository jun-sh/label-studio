#!/usr/bin/env bash
# Age-based session archive → cold tar.gz (7-day retention companion; no ingest code changes).
set -euo pipefail
ENV_FILE="${STREAM_STORAGE_ENV:-/opt/datalab/env/stream-storage.env}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi
STATION_ROOT="${STATION_ROOT:?set STATION_ROOT}"
COLD_ROOT="${COLD_ROOT:?set COLD_ROOT}"
HOT_ARCHIVE_MIN_AGE_HOURS="${HOT_ARCHIVE_MIN_AGE_HOURS:-168}"
LOG="${ARCHIVE_TO_COLD_LOG:-/opt/datalab/log/archive-to-cold.log}"
mkdir -p "$(dirname "$LOG")" "$COLD_ROOT"
ARCHIVE="${STATION_ROOT}/archive"
if [ ! -d "$ARCHIVE" ] || [ ! -r "$ARCHIVE" ]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  exec "$SCRIPT_DIR/migrate-archive-docker.sh" age
fi
mkdir -p "${STATION_ROOT}/archive"
exec >>"$LOG" 2>&1
echo "=== $(date -Is) archive-to-cold start cold=${COLD_ROOT} ==="
[ -d "$ARCHIVE" ] || { echo "no archive dir"; exit 0; }
MIN_AGE_SEC=$((HOT_ARCHIVE_MIN_AGE_HOURS * 3600))
NOW=$(date +%s)
shopt -s nullglob
for dir in "$ARCHIVE"/*/; do
  [ -d "$dir" ] || continue
  name=$(basename "$dir")
  mtime=$(stat -c %Y "$dir")
  age=$((NOW - mtime))
  if [ "$age" -lt "$MIN_AGE_SEC" ]; then
    echo "skip young archive: $name age=${age}s"
    continue
  fi
  out="${COLD_ROOT}/${name}.tar.gz"
  if [ -f "$out" ]; then
    echo "skip existing cold artifact: $out"
    rm -rf "$dir"
    continue
  fi
  echo "pack $dir -> $out"
  tar -czf "$out" -C "$ARCHIVE" "$name"
  rm -rf "$dir"
  echo "removed hot archive $name"
done
echo "=== $(date -Is) archive-to-cold done ==="
