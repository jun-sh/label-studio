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

# Parquet sync runs in stream-parquet-sync (uid 1000). Do not duplicate here as root —
# root-owned meta/ blocks stream-ingest imports (EACCES on info.json.tmp).

exec node /app/server.mjs
