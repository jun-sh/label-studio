#!/usr/bin/env bash
# Recreate derive-worker with P1 performance env (ego-001 MCAP production stack).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=ego-pipeline-performance.env.sh
source "$(dirname "$0")/ego-pipeline-performance.env.sh"

COMPOSE="${COMPOSE:-docker-compose}"
if ! command -v docker-compose >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
fi

cd "${ROOT}"
echo "[perf] DERIVE_EXTRACT_CONCURRENCY=${DERIVE_EXTRACT_CONCURRENCY}"
"${COMPOSE}" -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.v0.1.3-async.yml \
  -f data-lab-platform/docker-compose.v0.1.4-mcap.yml \
  up -d --force-recreate derive-worker

echo "[perf] derive-worker recreated — verify:"
docker exec data-lab-derive-worker-1 printenv DERIVE_EXTRACT_CONCURRENCY 2>/dev/null || true
echo "[perf] host convert defaults: EGO_WILOR_WARM=${EGO_WILOR_WARM} EGO_CONVERT_BATCH=${EGO_CONVERT_BATCH} EGO_DEPTH_PREVIEW=${EGO_DEPTH_PREVIEW} EGO_DEPTH_PREVIEW_STRIDE=${EGO_DEPTH_PREVIEW_STRIDE}"
