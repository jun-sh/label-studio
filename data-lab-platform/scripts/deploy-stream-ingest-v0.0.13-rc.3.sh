#!/usr/bin/env bash
# Deploy stream-ingest @ v0.0.13-rc.3 (EGO-001 Phase3 ingest module).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="${LEROBOT_IMAGE_TAG:-v0.0.13-rc.3}"
IMAGE="data-lab-lerobot-studio:${TAG}"

echo "=== build ${IMAGE} ==="
docker build -t "${IMAGE}" "${ROOT}/data-lab-platform/lerobot-studio"

echo "=== force-recreate stream-ingest derive-worker lerobot ==="
cd "${ROOT}"
COMPOSE=(docker compose)
if ! docker compose version &>/dev/null; then
  COMPOSE=(docker-compose)
fi
"${COMPOSE[@]}" -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/docker-compose.v0.0.13-rc.3.yml \
  up -d --force-recreate stream-ingest derive-worker lerobot

NGINX_CID="${NGINX_CONTAINER:-data-lab-nginx-1}"
if docker ps --format '{{.Names}}' | grep -q "^${NGINX_CID}$"; then
  docker restart "${NGINX_CID}" >/dev/null 2>&1 || true
  echo "nginx restarted (${NGINX_CID})"
fi

echo "=== verify ingest module in image ==="
docker exec data-lab-stream-ingest-1 node -e "
import fs from 'node:fs';
for (const f of ['receive-tar.mjs','segment-state.mjs','tar-validator.mjs','session-coordinator.mjs','index.mjs']) {
  const p = '/app/ingest/' + f;
  if (!fs.existsSync(p)) { console.error('missing', p); process.exit(1); }
}
await import('/app/ingest/index.mjs');
console.log('ingest module ok');
"

echo "Done. Image: ${IMAGE}"
