#!/usr/bin/env bash
# Deploy MCAP v0.1.4 production on 34 — ego-001 on main :7862 / derive-worker.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="${LEROBOT_IMAGE_TAG:-v0.1.4-mcap-rc}"
IMAGE="data-lab-lerobot-studio:${TAG}"

echo "[deploy-mcap] build image ${IMAGE}"
docker build -t "${IMAGE}" \
  -f "${ROOT}/data-lab-platform/lerobot-studio/Dockerfile" \
  "${ROOT}/data-lab-platform"

cd "${ROOT}"
COMPOSE="${COMPOSE:-docker-compose}"
if ! command -v docker-compose >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
fi

"${COMPOSE}" -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.v0.1.3-async.yml \
  -f data-lab-platform/docker-compose.v0.1.4-mcap.yml \
  up -d stream-ingest derive-worker lerobot

echo "[deploy-mcap] bootstrap ego-001 stream meta"
bash "${ROOT}/data-lab-platform/scripts/ego-mcap-bootstrap-34.sh"

# stream-ingest recreate changes container IP; nginx caches upstream DNS until reload.
NGINX_CONTAINER="${NGINX_CONTAINER:-data-lab-nginx-1}"
if docker ps --format '{{.Names}}' | grep -qx "${NGINX_CONTAINER}"; then
  echo "[deploy-mcap] restart ${NGINX_CONTAINER} (refresh stream-ingest upstream)"
  docker restart "${NGINX_CONTAINER}" >/dev/null
else
  echo "[deploy-mcap] skip nginx restart (${NGINX_CONTAINER} not running)"
fi

echo "Done. ego-001 MCAP production on :7862 (nginx :8080/lerobot)."
