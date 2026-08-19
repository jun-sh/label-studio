#!/usr/bin/env bash
# Deploy stream-ingest @ v0.0.13-rc.6 (EGO-001 Phase6 ops reset alignment).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="${LEROBOT_IMAGE_TAG:-v0.0.13-rc.6}"
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
  -f data-lab-platform/docker-compose.v0.0.13-rc.6.yml \
  up -d --force-recreate stream-ingest derive-worker lerobot

NGINX_CID="${NGINX_CONTAINER:-data-lab-nginx-1}"
if docker ps --format '{{.Names}}' | grep -q "^${NGINX_CID}$"; then
  docker restart "${NGINX_CID}" >/dev/null 2>&1 || true
  echo "nginx restarted (${NGINX_CID})"
fi

echo "=== verify Phase6 derive + reset scripts in repo ==="
test -f "${ROOT}/data-lab-platform/scripts/ego-reset-34-wipe-stream.sh"
test -f "${ROOT}/data-lab-platform/scripts/rc-ego-001-phase6.sh"

echo "=== verify derive modules in image ==="
docker exec data-lab-stream-ingest-1 node -e "
import fs from 'node:fs';
for (const f of ['ready-gate.mjs','lifecycle-gc.mjs','pipeline.mjs','index.mjs']) {
  if (!fs.existsSync('/app/derive/' + f)) { console.error('missing', f); process.exit(1); }
}
await import('/app/derive/index.mjs');
console.log('derive phase6 ok');
"

echo "Done. Image: ${IMAGE}"
