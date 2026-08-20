#!/usr/bin/env bash
# Build and deploy stream-ingest @ v0.1.1 (Phase D: unit-only derive).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ego-production-defaults.sh
source "${SCRIPT_DIR}/ego-production-defaults.sh"

ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TAG="${LEROBOT_IMAGE_TAG}"
IMAGE="data-lab-lerobot-studio:${TAG}"
STATION="${RC_STATION:-ego-001}"
TOKEN="${STATION_UPLOAD_TOKEN:-dl-upload-ego-001-v1}"

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
  -f "${EGO_COMPOSE_OVERLAY}" \
  up -d --force-recreate stream-ingest derive-worker lerobot

NGINX_CID="${NGINX_CONTAINER:-data-lab-nginx-1}"
if docker ps --format '{{.Names}}' | grep -q "^${NGINX_CID}$"; then
  docker restart "${NGINX_CID}" >/dev/null 2>&1 || true
  echo "nginx restarted (${NGINX_CID})"
fi

echo "=== verify v0.1.1 (Phase D) ==="
sleep 8
docker exec data-lab-stream-ingest-1 printenv DERIVE_LAYOUT STREAM_SESSION_SINGLE_EPISODE DERIVE_STANDALONE

docker exec data-lab-stream-ingest-1 node -e "
import fs from 'node:fs';
const checks = [
  ['/app/derive/pipeline.mjs', { must: ['runDerivePipelineUnit'], mustNot: ['buildFrameMapForDerive', 'mergeFrameMapIncremental'] }],
  ['/app/derive/frame-map.mjs', { mustNot: ['mergeFrameMapIncremental'] }],
  ['/app/derive/mux-exec.mjs', { mustNot: ['runFourCameraMuxIncremental', 'muxOneCameraIncremental'] }],
];
for (const [p, { must = [], mustNot = [] }] of checks) {
  const t = fs.readFileSync(p, 'utf8');
  for (const n of must) if (!t.includes(n)) { console.error('missing', n, 'in', p); process.exit(1); }
  for (const n of mustNot) if (t.includes(n)) { console.error('legacy still present:', n, 'in', p); process.exit(1); }
}
console.log('v0.1.1 Phase D image contents ok');
"

curl -sf --max-time 20 "http://127.0.0.1:8080/lerobot/api/collection/stations/${STATION}/derive-status" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); p=d.get('progress') or {}; print('derive-status phase',d.get('phase'),'rows',p.get('parquetRows',0))"

curl -sf -X POST "http://127.0.0.1:8080/lerobot/api/collection/stations/${STATION}/process-notify" \
  -H "X-Station-Token: ${TOKEN}" -H "Content-Type: application/json" -d '{}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('ok') and d.get('queued'); print('process-notify ok')"

rm -f "${ROOT}/data-storage/stream/${STATION}/state/process-notify.pending.json" \
      "${ROOT}/data-storage/stream/${STATION}/state/process-notify.running.json" 2>/dev/null || true

docker ps --format '{{.Names}} {{.Status}} {{.Image}}' | grep -E 'stream-ingest|derive-worker|lerobot'

echo "Done. Image: ${IMAGE}"
echo "Async mode: bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.1-async.sh"
