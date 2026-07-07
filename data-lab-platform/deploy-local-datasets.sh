#!/usr/bin/env bash
# One-shot: copy configs, ingest 4 datasets into lerobot volume, restart service.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

SAMPLES_DIR="${ROOT}/data-storage/samples"

compose() {
  docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml "$@"
}

if [ ! -d "${SAMPLES_DIR}" ]; then
  echo "ERROR: missing ${SAMPLES_DIR} — create it and add the four sample archives." >&2
  exit 1
fi

echo "==> Recreate lerobot with samples mount: ${SAMPLES_DIR}"
compose up -d lerobot

echo "==> Sync studio configs + branding into container"
docker cp data-lab-platform/lerobot-studio/config/sample-datasets.manifest.json data-lab-lerobot-1:/app/config/sample-datasets.manifest.json
docker cp data-lab-platform/lerobot-studio/config/datasets.json data-lab-lerobot-1:/app/config/datasets.json
docker cp data-lab-platform/lerobot-studio/config/collection-stations.json data-lab-lerobot-1:/app/config/collection-stations.json
docker cp data-lab-platform/lerobot-studio/branding data-lab-lerobot-1:/app/
docker cp data-lab-platform/lerobot-studio/server.mjs data-lab-lerobot-1:/app/server.mjs
docker cp data-lab-platform/lerobot-studio/stream-ingest.mjs data-lab-lerobot-1:/app/stream-ingest.mjs
docker cp data-lab-platform/lerobot-studio/task-naming.mjs data-lab-lerobot-1:/app/task-naming.mjs
docker cp data-lab-platform/lerobot-studio/ingest-server.mjs data-lab-lerobot-1:/app/ingest-server.mjs 2>/dev/null || true
docker cp data-lab-platform/lerobot-studio/server.mjs data-lab-lerobot-1:/app/server.mjs
docker cp data-lab-platform/lerobot-studio/scripts data-lab-lerobot-1:/app/scripts
docker cp data-lab-platform/lerobot-studio/patches/apply-branding.sh data-lab-lerobot-1:/app/patches/apply-branding.sh
docker cp data-lab-platform/lerobot-studio/patches/patch-dockview-scalar-chart.sh data-lab-lerobot-1:/app/patches/patch-dockview-scalar-chart.sh
docker exec data-lab-lerobot-1 chmod +x /app/patches/patch-dockview-scalar-chart.sh
docker cp data-lab-platform/lerobot-studio/ingest-bundled-datasets.sh data-lab-lerobot-1:/app/ingest-bundled-datasets.sh
docker exec data-lab-lerobot-1 chmod +x /app/ingest-bundled-datasets.sh
docker exec data-lab-lerobot-1 sh /app/patches/apply-branding.sh /srv/lerobot

echo "==> Ingest datasets (may take a few minutes for DualPiper tar)..."
docker exec data-lab-lerobot-1 sh /app/ingest-bundled-datasets.sh

echo "==> Restart lerobot + nginx"
compose restart lerobot nginx

echo "==> Wait for lerobot embed route"
HOST="${LABEL_STUDIO_HOST:-http://10.10.10.34:8080}"
for _ in $(seq 1 24); do
  if curl -fsS --max-time 5 "${HOST}/lerobot/?datalab_embed=1" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

echo "==> Verify /data page (embed manifest + bundled datasets)"
bash "${ROOT}/data-lab-platform/verify-data-page.sh" || exit 1

echo "==> Live stream parquet helper (optional, during capture):"
echo "    bash data-lab-platform/scripts/sync-stream-station.sh ego-lan-214"

echo "==> Done. Open http://10.10.10.34:8080/collection"
