#!/usr/bin/env bash
# Deploy stream-ingest + derive-worker @ v0.0.9.2 (ego-001 segment_mp4 path).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="${LEROBOT_IMAGE_TAG:-v0.0.9.2}"
IMAGE="data-lab-lerobot-studio:${TAG}"

echo "=== build ${IMAGE} ==="
docker build -t "${IMAGE}" "${ROOT}/data-lab-platform/lerobot-studio"

echo "=== up stream-ingest derive-worker lerobot ==="
cd "${ROOT}"
docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/deploy/archive/compose/docker-compose.v0.0.9.2.yml \
  up -d stream-ingest derive-worker lerobot

echo "=== verify image tags ==="
for svc in stream-ingest derive-worker; do
  cid="$(docker compose -f docker-compose.yml \
    -f data-lab-platform/docker-compose.platform.yml \
    -f data-lab-platform/docker-compose.storage.override.yml \
    -f data-lab-platform/deploy/archive/compose/docker-compose.v0.0.9.2.yml \
    ps -q "${svc}" 2>/dev/null || true)"
  if [[ -n "${cid}" ]]; then
    docker inspect --format '{{.Name}} image={{.Config.Image}}' "${cid}"
    docker exec "${cid}" grep -c withMp4ConcatLock /app/segment-mp4-ingest.mjs 2>/dev/null \
      && echo "  segment-mp4-ingest: v0.0.9.2 markers ok" || true
  fi
done

echo "=== done; run: RC_STATION=ego-001 ${ROOT}/data-lab-platform/scripts/rc-acceptance.sh ==="

# stream-ingest / lerobot recreate can swap Docker IPs; restart gateway nginx.
NGINX_CID="${NGINX_CONTAINER:-data-lab-nginx-1}"
if docker ps --format '{{.Names}}' | grep -q "^${NGINX_CID}$"; then
  docker restart "${NGINX_CID}" >/dev/null 2>&1 || true
  echo "nginx restarted (${NGINX_CID})"
fi
