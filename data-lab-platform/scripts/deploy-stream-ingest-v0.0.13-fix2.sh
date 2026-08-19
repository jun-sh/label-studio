#!/usr/bin/env bash
# Deploy stream-ingest @ v0.0.13-fix2 (fix1 + IMU PAIR_TOLERANCE_NS 3ms).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="${LEROBOT_IMAGE_TAG:-v0.0.13-fix2}"
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
  -f data-lab-platform/docker-compose.v0.0.13-fix2.yml \
  up -d --force-recreate stream-ingest derive-worker lerobot

NGINX_CID="${NGINX_CONTAINER:-data-lab-nginx-1}"
if docker ps --format '{{.Names}}' | grep -q "^${NGINX_CID}$"; then
  docker restart "${NGINX_CID}" >/dev/null 2>&1 || true
  echo "nginx restarted (${NGINX_CID})"
fi

echo "=== verify IMU pair tolerance in image ==="
docker exec data-lab-stream-ingest-1 python3 -c "
import re
text = open('/app/derive/imu/ingest-raw.py').read()
m = re.search(r'PAIR_TOLERANCE_NS\s*=\s*([\d_]+)', text)
if not m or int(m.group(1).replace('_', '')) != 3000000:
    raise SystemExit('PAIR_TOLERANCE_NS != 3_000_000')
print('ingest-raw PAIR_TOLERANCE_NS=3ms ok')
"

docker exec data-lab-stream-ingest-1 node -e "
import fs from 'node:fs';
for (const f of ['ready-gate.mjs','lifecycle-gc.mjs','pipeline.mjs','frame-map.mjs','index.mjs']) {
  if (!fs.existsSync('/app/derive/' + f)) { console.error('missing', f); process.exit(1); }
}
await import('/app/derive/index.mjs');
console.log('derive v0.0.13-fix2 ok');
"

echo "Done. Image: ${IMAGE} (fix1 + IMU pair tolerance 3ms)"
