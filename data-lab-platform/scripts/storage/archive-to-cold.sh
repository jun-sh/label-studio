#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
source /opt/datalab/env/stream-storage.env
LOG=/opt/datalab/log/archive-to-cold.log
mkdir -p "$(dirname "$LOG")" "$COLD_ROOT" "${STATION_ROOT}/archive"
exec >>"$LOG" 2>&1
echo "=== $(date -Is) archive-to-cold start ==="
ARCHIVE="${STATION_ROOT}/archive"
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
