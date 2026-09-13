#!/usr/bin/env bash
# Deploy v0.2.0-rectified-720p-l2 to 34 (docker) + 130 (MCAP 720p crop capture).
#
# Usage:
#   RC_CAPTURE_PASS=1 bash data-lab-platform/scripts/deploy-v0.2.0-rectified-720p-l2.sh [station] [130_target]
#
# Options:
#   EGO_SKIP_CORPUS_ABANDON=1   skip archiving pre-720p corpus/samples
#   EGO_SKIP_130=1              skip 130 provision
#   EGO_SKIP_STRESS=1           skip post-deploy ego-stress-5min
set -euo pipefail

STATION="${1:-ego-001}"
TARGET="${2:-server@10.10.10.130}"
TAG="v0.2.0-rectified-720p-l2"
IMAGE="data-lab-lerobot-studio:${TAG}"

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)"
SCRIPT_DIR="${ROOT}/data-lab-platform/scripts"
# shellcheck source=ego-production-defaults.sh
source "${SCRIPT_DIR}/ego-production-defaults.sh"

COMPOSE=(docker compose)
if ! docker compose version &>/dev/null; then
  COMPOSE=(docker-compose)
fi

log() { echo "[deploy-${TAG}] $*"; }

SLUG="$(python3 "${SCRIPT_DIR}/ego-pipeline-sessions.py" slug "${STATION}" --datalab-root "${ROOT}" 2>/dev/null || echo "ego_001")"

log "=== 0/5 abandon pre-720p corpus (rename archive) ==="
if [[ "${EGO_SKIP_CORPUS_ABANDON:-0}" != "1" ]]; then
  bash "${SCRIPT_DIR}/ego-corpus-abandon-pre-720p.sh" "${SLUG}"
else
  log "skipped (EGO_SKIP_CORPUS_ABANDON=1)"
fi

log "=== 1/5 build ${IMAGE} ==="
docker build -t "${IMAGE}" \
  -f "${ROOT}/data-lab-platform/lerobot-studio/Dockerfile" \
  "${ROOT}/data-lab-platform"

log "=== 2/5 recreate 34 stream-ingest + derive-worker + lerobot ==="
(
  cd "${ROOT}"
  mapfile -t COMPOSE_FILES < <(ego_compose_files_for_mode manual "${ROOT}")
  args=()
  for f in "${COMPOSE_FILES[@]}"; do
    args+=(-f "$f")
  done
  "${COMPOSE[@]}" "${args[@]}" up -d --force-recreate stream-ingest derive-worker lerobot
)

printf 'manual\n' > "${ROOT}/data-lab-platform/.ego-derive-mode"
docker stop data-lab-derive-worker-1 >/dev/null 2>&1 || true
bash "${SCRIPT_DIR}/ego-mcap-bootstrap-34.sh"

NGINX_CONTAINER="${NGINX_CONTAINER:-data-lab-nginx-1}"
if docker ps --format '{{.Names}}' | grep -qx "${NGINX_CONTAINER}"; then
  log "restart ${NGINX_CONTAINER} (refresh stream-ingest upstream)"
  docker restart "${NGINX_CONTAINER}" >/dev/null
fi

if [[ "${EGO_SKIP_130:-0}" != "1" ]]; then
  log "=== 3/5 provision 130 MCAP 720p capture stack (normalized crop hotfix) ==="
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" \
    bash "${SCRIPT_DIR}/ego-130-provision-720p-l2.sh" "${TARGET}" "${STATION}"
else
  log "=== 3/5 skipped 130 (EGO_SKIP_130=1) ==="
fi

log "=== 4/5 verify doctor + release-check ==="
RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" bash "${SCRIPT_DIR}/ego-station-doctor.sh" "${STATION}" "${TARGET}"
RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" bash "${SCRIPT_DIR}/ego-release-check.sh" "${STATION}" "${TARGET}"

if [[ "${EGO_SKIP_STRESS:-0}" != "1" ]]; then
  log "=== 5/5 post-deploy ego-stress-5min (720p + rectify) ==="
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" bash "${SCRIPT_DIR}/ego-stress-5min.sh" "${STATION}" "${TARGET}" 2>&1 | tee \
    "${ROOT}/data-storage/logs/deploy-${TAG}-stress-$(date +%Y%m%d-%H%M%S).log"
else
  log "=== 5/5 skipped stress (EGO_SKIP_STRESS=1) ==="
fi

log "Done. tag=${TAG} station=${STATION} derive_mode=manual"
log "34: stream-ingest + lerobot on ${IMAGE}; EGO_OAK_MODE=rectify corpus=1280x720"
log "130: ${TARGET} MCAP center-crop 720p — re-record required (old 800p corpus archived)"
log "SOP: 130 record/upload → 34: ego-process ${STATION}"
