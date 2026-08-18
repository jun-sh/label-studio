#!/usr/bin/env bash
# Deploy stream-ingest + derive-worker + lerobot @ v0.0.11.2 (ego-standard Plan B).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="${LEROBOT_IMAGE_TAG:-v0.0.11.2}"
IMAGE="data-lab-lerobot-studio:${TAG}"

echo "=== build ${IMAGE} ==="
docker build -t "${IMAGE}" "${ROOT}/data-lab-platform/lerobot-studio"

echo "=== force-recreate stream-ingest derive-worker lerobot ==="
cd "${ROOT}"
docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/docker-compose.v0.0.11.2.yml \
  up -d --force-recreate stream-ingest derive-worker lerobot

NGINX_CID="${NGINX_CONTAINER:-data-lab-nginx-1}"
if docker ps --format '{{.Names}}' | grep -q "^${NGINX_CID}$"; then
  docker restart "${NGINX_CID}" >/dev/null 2>&1 || true
  echo "nginx restarted (${NGINX_CID})"
fi

echo "=== verify topology + ingest markers ==="
docker exec data-lab-stream-ingest-1 node -e "
import fs from 'node:fs';
const topo = JSON.parse(fs.readFileSync('/app/config/station-topology.json','utf8'));
const keys = topo['ego-standard'].video_keys;
const want = [
  'observation.images.camera_front_left',
  'observation.images.camera_front_right',
  'observation.images.camera_rear_left',
  'observation.images.camera_rear_right',
];
if (JSON.stringify(keys) !== JSON.stringify(want)) {
  console.error('FAIL ego-standard keys', keys);
  process.exit(1);
}
console.log('ego-standard video_keys ok');
import { legacyStagingMuxEnabled } from '/app/segment-mp4-ingest.mjs';
if (!legacyStagingMuxEnabled()) process.exit(1);
console.log('staging_mux primary ok');
"

docker inspect data-lab-derive-worker-1 --format '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' \
  | grep -c '\.mjs$' && echo "WARN: derive-worker still has .mjs bind-mounts" || echo "derive-worker image-only ok"

echo "Done. Run: RC_STATION=ego-001 ${ROOT}/data-lab-platform/scripts/ego-001-plan-b-e2e.sh"
