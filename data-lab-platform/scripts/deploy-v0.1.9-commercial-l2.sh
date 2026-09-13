#!/usr/bin/env bash
# Deploy v0.1.9-commercial-l2 to 34 (docker) + 130 (MCAP capture stack).
# Usage: RC_CAPTURE_PASS=1 bash data-lab-platform/scripts/deploy-v0.1.9-commercial-l2.sh [station] [130_target]
set -euo pipefail

STATION="${1:-ego-001}"
TARGET="${2:-server@10.10.10.130}"
TAG="v0.1.9-commercial-l2"
IMAGE="data-lab-lerobot-studio:${TAG}"

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)"
SCRIPT_DIR="${ROOT}/data-lab-platform/scripts"

COMPOSE=(docker compose)
if ! docker compose version &>/dev/null; then
  COMPOSE=(docker-compose)
fi

log() { echo "[deploy-${TAG}] $*"; }

log "=== 1/4 build ${IMAGE} ==="
docker build -t "${IMAGE}" \
  -f "${ROOT}/data-lab-platform/lerobot-studio/Dockerfile" \
  "${ROOT}/data-lab-platform"

log "=== 2/4 recreate 34 stream-ingest + derive-worker + lerobot ==="
(
  cd "${ROOT}"
  "${COMPOSE[@]}" \
    -f docker-compose.yml \
    -f data-lab-platform/docker-compose.platform.yml \
    -f data-lab-platform/docker-compose.storage.override.yml \
    -f data-lab-platform/docker-compose.v0.1.9-commercial-l2.yml \
    up -d --force-recreate stream-ingest derive-worker lerobot
)

printf 'manual\n' > "${ROOT}/data-lab-platform/.ego-derive-mode"
docker stop data-lab-derive-worker-1 >/dev/null 2>&1 || true
bash "${SCRIPT_DIR}/ego-mcap-bootstrap-34.sh"

NGINX_CONTAINER="${NGINX_CONTAINER:-data-lab-nginx-1}"
if docker ps --format '{{.Names}}' | grep -qx "${NGINX_CONTAINER}"; then
  log "restart ${NGINX_CONTAINER} (refresh stream-ingest upstream)"
  docker restart "${NGINX_CONTAINER}" >/dev/null
fi

log "=== 3/4 provision 130 MCAP stack ==="
RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" \
  bash "${SCRIPT_DIR}/ego-130-provision-mcap-production.sh" "${TARGET}" "${STATION}"

log "=== 4/4 verify doctor + release-check ==="
RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" bash "${SCRIPT_DIR}/ego-station-doctor.sh" "${STATION}" "${TARGET}"
RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" bash "${SCRIPT_DIR}/ego-release-check.sh" "${STATION}" "${TARGET}"

log "Done. tag=${TAG} station=${STATION} derive_mode=manual"
log "34: stream-ingest + lerobot on ${IMAGE}"
log "130: ${TARGET} MCAP capture aligned — phone UI :8080"
