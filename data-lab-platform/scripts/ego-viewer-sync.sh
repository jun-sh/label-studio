#!/usr/bin/env bash
# Light Viewer sync — copy samples → bundled volume (no container recreate).
set -euo pipefail

LEROBOT_CONTAINER="${LEROBOT_CONTAINER:-data-lab-lerobot-1}"

if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${LEROBOT_CONTAINER}$"; then
  docker exec "$LEROBOT_CONTAINER" sh /app/ingest-bundled-datasets.sh
else
  echo "[viewer-sync] container ${LEROBOT_CONTAINER} not running" >&2
  exit 1
fi
