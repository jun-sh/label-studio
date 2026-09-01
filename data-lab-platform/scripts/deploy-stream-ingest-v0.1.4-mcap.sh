#!/usr/bin/env bash
# Deploy MCAP v0.1.4 production on 34 — ego-001 on main :7862 / derive-worker.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="${LEROBOT_IMAGE_TAG:-v0.1.4-mcap-rc}"
IMAGE="data-lab-lerobot-studio:${TAG}"

echo "[deploy-mcap] build image ${IMAGE}"
docker build -t "${IMAGE}" "${ROOT}/data-lab-platform/lerobot-studio"

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

echo "Done. ego-001 MCAP production on :7862 (nginx :8080/lerobot)."
