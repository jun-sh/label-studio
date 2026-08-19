#!/usr/bin/env bash
# Wipe 34 platform derived data for a station; preserve raw/segments tar.zst archives.
# Phase0 §9.3 — does NOT SSH to edge.
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SAMPLES_SLUG="${SAMPLES_SLUG:-$(python3 "${SCRIPT_DIR}/ego-pipeline-sessions.py" slug "${STATION}" --datalab-root "$(cd "${SCRIPT_DIR}/../.." && pwd)" 2>/dev/null || echo egodome)}"
AGGREGATE_NAME="${AGGREGATE_NAME:-EgoDome}"

ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PIPE_ROOT="${EGO_HAND_PIPELINE_ROOT:-$(dirname "$ROOT")/ego-hand-pipeline}"
STREAM_HOST="${EGO_STREAM_HOST:-${ROOT}/data-storage/stream/${STATION}}"
ARCHIVE_HOST="${ROOT}/data-storage/ego-archive/${STATION}"
STREAM_DEV="${ROOT}/data-lab-platform/stream-data/${STATION}"
COMPOSE_OVERLAY="${EGO_COMPOSE_OVERLAY:-data-lab-platform/docker-compose.v0.0.13.yml}"

# shellcheck source=ego-reset-34-wipe-stream.sh
source "${SCRIPT_DIR}/ego-reset-34-wipe-stream.sh"

log() { echo "[reset-34] $*"; }
die() { echo "[reset-34] ERROR: $*" >&2; exit 1; }

confirm() {
  if [[ "${EGO_RESET_YES:-}" == "1" ]]; then
    return 0
  fi
  if [[ "${1:-}" == "--yes" ]]; then
    return 0
  fi
  echo "DELETE 34-side DERIVED data for ${STATION} (data/videos/sensor_raw/staging/live/state)."
  echo "PRESERVE raw/segments/*.tar.zst. Edge segments are NOT wiped."
  echo "Set EGO_RESET_YES=1 to skip prompt."
  read -r -p "Continue? [y/N] " ans
  [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]]
}

[[ "${1:-}" != "--yes" ]] || shift
confirm "${1:-}" || die "aborted"

log "=== 34: wipe derived stream (preserve raw/segments) ==="
mkdir -p "${STREAM_HOST}/raw/segments"
if ! ego_reset_wipe_stream_derived "${STREAM_HOST}" "${STATION}" 2>/dev/null; then
  log "host wipe partial — retry via docker"
  ego_reset_wipe_stream_derived_docker "${ROOT}/data-storage/stream" "${STATION}"
fi
rm -rf "${STREAM_DEV}" 2>/dev/null || true
rm -rf "${ARCHIVE_HOST}"
mkdir -p "${ARCHIVE_HOST}"

log "=== 34: wipe pipeline ==="
rm -rf "${ROOT}/data-storage/pipeline/${STATION}"

log "=== 34: wipe corpus + samples (${SAMPLES_SLUG}) ==="
rm -rf "${ROOT}/data-storage/corpus/${SAMPLES_SLUG}"
rm -f "${ROOT}/data-storage/corpus/${SAMPLES_SLUG}.zip"
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
if [[ -f "${ROOT}/${COMPOSE_OVERLAY}" ]]; then
  "${COMPOSE[@]}" -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml \
    -f data-lab-platform/docker-compose.storage.override.yml \
    -f "${COMPOSE_OVERLAY}" \
    restart stream-ingest derive-worker lerobot nginx >/dev/null 2>&1 || true
else
  "${COMPOSE[@]}" -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml \
    -f data-lab-platform/docker-compose.storage.override.yml \
    restart stream-ingest derive-worker lerobot nginx >/dev/null 2>&1 || true
fi

sleep 2
raw_count="$(find "${STREAM_HOST}/raw/segments" -name '*.tar.zst' 2>/dev/null | wc -l | tr -d ' ')"
frames="$(curl -sf "http://127.0.0.1:8080/lerobot/api/stream/${STATION}/status" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('totalFrames', '?'))" 2>/dev/null || echo "?")"
log "raw archives preserved: ${raw_count}; stream totalFrames=${frames} (expect 0 after re-derive)"
log "done — re-upload/derive from raw: ego-derive run ${STATION} or upload_segments --force"
