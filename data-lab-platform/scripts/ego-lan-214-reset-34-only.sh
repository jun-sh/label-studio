#!/usr/bin/env bash
# Wipe 34 platform data for ego-lan-214 (stream + pipeline + corpus + samples).
# Does NOT SSH to 214 — edge segments/export remain for re-upload.
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
SAMPLES_SLUG="${SAMPLES_SLUG:-ego_214_hand_pose}"
AGGREGATE_NAME="${AGGREGATE_NAME:-Ego-214-Hand-Pose}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PIPE_ROOT="${EGO_HAND_PIPELINE_ROOT:-$(dirname "$ROOT")/ego-hand-pipeline}"
STREAM_HOST="${ROOT}/data-storage/stream/${STATION}"
ARCHIVE_HOST="${ROOT}/data-storage/ego-archive/${STATION}"
STREAM_DEV="${ROOT}/data-lab-platform/stream-data/${STATION}"

log() { echo "[reset-34] $*"; }
die() { echo "[reset-34] ERROR: $*" >&2; exit 1; }

confirm() {
  if [[ "${EGO_RESET_YES:-}" == "1" ]]; then
    return 0
  fi
  if [[ "${1:-}" == "--yes" ]]; then
    return 0
  fi
  echo "This will DELETE 34-side data for ${STATION}:"
  echo "  stream, pipeline, corpus, samples (Viewer publish artifacts)"
  echo ""
  echo "SAFE: does NOT wipe 214 segments/export — you can re-upload from edge."
  echo "Set EGO_RESET_YES=1 to skip this prompt."
  read -r -p "Continue? [y/N] " ans
  [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]]
}

[[ "${1:-}" != "--yes" ]] || shift
confirm "${1:-}" || die "aborted"

log "=== 34: wipe stream ==="
if ! rm -rf "${STREAM_HOST}" 2>/dev/null; then
  docker run --rm -v "${ROOT}/data-storage/stream:/srv/stream" alpine \
    sh -c "rm -rf /srv/stream/${STATION} && mkdir -p /srv/stream/${STATION} && chown 1000:1000 /srv/stream/${STATION}"
else
  mkdir -p "${STREAM_HOST}"
fi
rm -rf "${STREAM_DEV}" 2>/dev/null || true
rm -rf "${ARCHIVE_HOST}"
mkdir -p "${ARCHIVE_HOST}"

log "=== 34: wipe pipeline ==="
rm -rf "${ROOT}/data-storage/pipeline/${STATION}"

log "=== 34: wipe corpus + samples ==="
rm -rf "${ROOT}/data-storage/corpus/${SAMPLES_SLUG}"
rm -f "${ROOT}/data-storage/corpus/${SAMPLES_SLUG}.zip"
rm -rf "${ROOT}/data-storage/embodied-annotate/datasets/ego_214"
rm -rf "${ROOT}/data-storage/stream/delivery_samples/ego_dual_v1"
rm -rf "${ROOT}/data-storage/samples/${SAMPLES_SLUG}"*
rm -f "${ROOT}/data-storage/samples/${SAMPLES_SLUG}.zip"*
rm -f "${ROOT}/data-storage/samples/${SAMPLES_SLUG}_hand_kp2d.json"
rm -f "${ROOT}/data-storage/samples/${SAMPLES_SLUG}_depth_preview.json"
rm -rf "${ROOT}/data-storage/samples/${SAMPLES_SLUG}_depth_preview_frames"

if [[ -d "${PIPE_ROOT}/outputs" ]]; then
  rm -rf "${PIPE_ROOT}/outputs/${STATION}"
  rm -rf "${PIPE_ROOT}/outputs/dataset/${AGGREGATE_NAME}"
  rm -f "${PIPE_ROOT}/outputs/dataset/${SAMPLES_SLUG}.zip"
fi

log "=== Docker: restart ingest services ==="
COMPOSE=(docker compose)
if ! docker compose version &>/dev/null; then
  COMPOSE=(docker-compose)
fi
cd "${ROOT}"
"${COMPOSE[@]}" -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml \
  restart stream-ingest derive-worker lerobot nginx >/dev/null 2>&1 || \
"${COMPOSE[@]}" -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml \
  restart stream-ingest lerobot nginx >/dev/null 2>&1 || true

sleep 2
frames="$(curl -sf "http://127.0.0.1:8080/lerobot/api/stream/${STATION}/status" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('totalFrames', '?'))" 2>/dev/null || echo "?")"
log "stream totalFrames=${frames} (expect 0)"
log "done — re-upload from 214, then: ego-deliver ${STATION}"
