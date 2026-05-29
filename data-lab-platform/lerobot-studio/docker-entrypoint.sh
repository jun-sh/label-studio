#!/bin/sh
set -eu

ROOT="${LEROBOT_STUDIO_ROOT:-/srv/lerobot}"
export LEROBOT_STUDIO_ROOT="$ROOT"

if [ ! -f "${ROOT}/assets/index-BM8rEaYC.js" ]; then
  echo "==> Downloading visualizer static assets into ${ROOT}"
  /app/download-assets.sh
fi

sh /app/patches/apply-branding.sh "${ROOT}"
if sh /app/ingest-bundled-datasets.sh; then
  echo "==> Using ingested LeRobot v3 datasets"
else
  echo "==> Ingest skipped/failed; falling back to mini bundled sample"
  sh /app/prepare-bundled-dataset.sh
fi

if command -v python3 >/dev/null 2>&1 && [ -f /app/scripts/sync-stream-parquet.py ]; then
  (
    while true; do
      for station_dir in /srv/stream/*/; do
        [ -d "$station_dir" ] || continue
        [ -f "$station_dir/data/chunk-000/file-000.jsonl" ] || continue
        python3 /app/scripts/sync-stream-parquet.py "$station_dir" >/dev/null 2>&1 || true
      done
      sleep 4
    done
  ) &
fi

exec node /app/server.mjs
