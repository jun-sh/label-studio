#!/usr/bin/env bash
# Deploy MCAP pilot overlay on 34 (skeleton — P2 implements ingest/derive MCAP path).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="${LEROBOT_IMAGE_TAG:-v0.1.4-mcap-rc}"
IMAGE="data-lab-lerobot-studio:${TAG}"

echo "[deploy-mcap-pilot] build image ${IMAGE} (reuses v0.1.3 base until P2 code lands)"
docker build -t "${IMAGE}" "${ROOT}/data-lab-platform/lerobot-studio"

cd "${ROOT}"
docker compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.v0.1.4-mcap-pilot.yml \
  --profile mcap-pilot up -d stream-ingest-mcap-pilot derive-worker-mcap-pilot

echo "Done. Pilot ingest :7863 (production :7862 unchanged)."
