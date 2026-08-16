#!/usr/bin/env bash
# Deploy stream-ingest + derive-worker @ v0.0.10 (image-only derive-worker).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="${LEROBOT_IMAGE_TAG:-v0.0.10}"
IMAGE="data-lab-lerobot-studio:${TAG}"

echo "=== build ${IMAGE} ==="
docker build -t "${IMAGE}" "${ROOT}/data-lab-platform/lerobot-studio"

echo "=== up stream-ingest derive-worker lerobot ==="
cd "${ROOT}"
docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/docker-compose.v0.0.10.yml \
  up -d stream-ingest derive-worker lerobot

NGINX_CID="${NGINX_CONTAINER:-data-lab-nginx-1}"
if docker ps --format '{{.Names}}' | grep -q "^${NGINX_CID}$"; then
  docker restart "${NGINX_CID}" >/dev/null 2>&1 || true
  echo "nginx restarted (${NGINX_CID})"
fi

echo "=== verify ==="
docker inspect data-lab-derive-worker-1 --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' \
  | grep -c '\.mjs$' && echo "WARN: derive-worker still has .mjs bind-mounts" || echo "derive-worker image-only ok"

echo "Run: RC_STATION=ego-001 ${ROOT}/data-lab-platform/scripts/ci-ego-platform.sh"
