#!/usr/bin/env bash
# Deploy MCAP Track2 overlay on 34 (:7864). Pilot :7863 and production :7862 unchanged.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="${LEROBOT_IMAGE_TAG:-v0.1.4-mcap-rc}"
IMAGE="data-lab-lerobot-studio:${TAG}"

echo "[deploy-mcap-track2] build image ${IMAGE}"
docker build -t "${IMAGE}" "${ROOT}/data-lab-platform/lerobot-studio"

cd "${ROOT}"
COMPOSE="${COMPOSE:-docker-compose}"
if ! command -v docker-compose >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
fi
"${COMPOSE}" -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.v0.1.4-mcap-track2.yml \
  --profile mcap-track2 up -d stream-ingest-mcap-track2 derive-worker-mcap-track2

echo "Done. Track2 ingest :7864 (pilot :7863, production :7862 unchanged)."
echo "Bootstrap: bash data-lab-platform/scripts/ego-mcap-track2-bootstrap-34.sh"
