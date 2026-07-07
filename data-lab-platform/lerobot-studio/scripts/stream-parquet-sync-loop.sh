#!/bin/sh
# Periodic parquet sync for stream stations (skips listed stations — Phase-1 ego-lan-214).
set -eu

INTERVAL="${STREAM_SYNC_INTERVAL:-5}"
SKIP_RAW="${STREAM_PARQUET_SYNC_SKIP_STATIONS:-}"
ROOT="${STREAM_DATA_ROOT:-/srv/stream}"

station_skipped() {
  station="$1"
  if [ -z "$SKIP_RAW" ]; then
    return 1
  fi
  old_ifs=$IFS
  IFS=,
  for item in $SKIP_RAW; do
  item=$(echo "$item" | tr -d ' ')
    if [ "$item" = "$station" ]; then
      IFS=$old_ifs
      return 0
    fi
  done
  IFS=$old_ifs
  return 1
}

while true; do
  for d in "$ROOT"/*/; do
    [ -d "$d" ] || continue
    station=$(basename "$d")
    if station_skipped "$station"; then
      continue
    fi
    if [ -f "${d}data/chunk-000/file-000.jsonl" ] || [ -f "${d}meta/info.json" ]; then
      python3 /scripts/sync-stream-parquet.py --meta-only "$d" || true
    fi
  done
  sleep "$INTERVAL"
done
