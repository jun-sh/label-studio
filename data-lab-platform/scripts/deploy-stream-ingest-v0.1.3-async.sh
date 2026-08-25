#!/usr/bin/env bash
# Deploy v0.1.3 with P-Ops-2 async derive-worker enabled.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ego-production-defaults.sh
source "${SCRIPT_DIR}/ego-production-defaults.sh"

ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TAG="${LEROBOT_IMAGE_TAG}"

echo "=== build ${TAG} (if needed) ==="
docker build -t "data-lab-lerobot-studio:${TAG}" "${ROOT}/data-lab-platform/lerobot-studio"

echo "=== enable async derive mode ==="
LEROBOT_IMAGE_TAG="${TAG}" bash "${ROOT}/data-lab-platform/scripts/ego-derive-mode.sh" async

echo "=== verify async v0.1.3 ==="
sleep 5
docker exec data-lab-stream-ingest-1 printenv DERIVE_ASYNC DERIVE_ASYNC_EGO_001 DERIVE_LAYOUT
docker ps --format '{{.Names}} {{.Status}} {{.Image}}' | grep -E 'stream-ingest|derive-worker|lerobot'

curl -sf --max-time 20 'http://127.0.0.1:8080/lerobot/api/collection/stations/ego-001/derive-status' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('phase',d.get('phase'),'async',d.get('asyncEnabled'))"

echo "Done. Image: data-lab-lerobot-studio:${TAG} · mode: async"
echo "SOP: 130 ego-upload ego-001 [--notify] → 34 ego-process ego-001"
