#!/usr/bin/env bash
# One-shot reset for a full ego-lan-214 re-run (stage 0 in pipeline runbook).
# Run on Data Lab host (10.10.10.34). SSH to 214 required.
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
EDGE_HOST="${EDGE_HOST:-server@10.10.10.214}"
SAMPLES_SLUG="${SAMPLES_SLUG:-ego_214_hand_pose}"
AGGREGATE_NAME="${AGGREGATE_NAME:-Ego-214-Hand-Pose}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PIPE_ROOT="${EGO_HAND_PIPELINE_ROOT:-$(dirname "$ROOT")/ego-hand-pipeline}"
STREAM_HOST="${ROOT}/data-storage/stream/${STATION}"
ARCHIVE_HOST="${ROOT}/data-storage/ego-archive/${STATION}"
STREAM_DEV="${ROOT}/data-lab-platform/stream-data/${STATION}"

log() { echo "[reset] $*"; }
die() { echo "[reset] ERROR: $*" >&2; exit 1; }

confirm() {
  if [[ "${EGO_RESET_YES:-}" == "1" ]]; then
    return 0
  fi
  if [[ "${1:-}" == "--yes" ]]; then
    return 0
  fi
  echo "This will DELETE all ego-lan-214 data on 214 + 34 + pipeline outputs + published samples."
  echo "Set EGO_RESET_YES=1 to skip this prompt."
  read -r -p "Continue? [y/N] " ans
  [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]]
}

[[ "${1:-}" != "--yes" ]] || shift
confirm "${1:-}" || die "aborted"

log "=== 214: stop capture (best effort) + wipe segments/export/shm ==="
ssh -o BatchMode=yes "${EDGE_HOST}" bash -s <<'REMOTE' || die "SSH to 214 failed (${EDGE_HOST})"
set -euo pipefail
rm -f "${HOME}/.config/systemd/user/ecs-station-heartbeat.service.d/capture-stack.conf"
systemctl --user daemon-reload 2>/dev/null || true
systemctl --user stop ecs-oak-capture-stack.target 2>/dev/null || true
systemctl stop ecs-oak-capture-stack.target 2>/dev/null || true
systemctl stop ecs-record-oak-stream.service 2>/dev/null || true

rm -rf /home/server/cache/ego-lan-214/segments/sessions
rm -f /home/server/cache/ego-lan-214/segments/registry.json
rm -f /home/server/cache/ego-lan-214/segments/checkpoint.json
rm -f /home/server/cache/ego-lan-214/segments/strict_emit_ts.json
mkdir -p /home/server/cache/ego-lan-214/segments

rm -rf /home/server/cache/ego-lan-214/logs/*
rm -rf /home/server/export/ego-lan-214
rm -rf /dev/shm/ego-capture-active 2>/dev/null || true

echo "214 segments: $(du -sh /home/server/cache/ego-lan-214/segments 2>/dev/null | cut -f1)"

# Heartbeat is independent of capture stack — required for collection "Live" mode.
systemctl --user enable ecs-station-heartbeat.service 2>/dev/null || true
systemctl --user start ecs-station-heartbeat.service 2>/dev/null || true
systemctl --user enable ecs-oak-standby-stack.target 2>/dev/null || true
systemctl --user start ecs-oak-standby-stack.target 2>/dev/null || true
REMOTE

log "=== 34: wipe stream (collection page source) ==="
if ! rm -rf "${STREAM_HOST}" 2>/dev/null; then
  docker run --rm -v "${ROOT}/data-storage/stream:/srv/stream" alpine \
    sh -c "rm -rf /srv/stream/${STATION} && mkdir -p /srv/stream/${STATION} && chown 1000:1000 /srv/stream/${STATION}"
else
  mkdir -p "${STREAM_HOST}"
fi

rm -rf "${STREAM_DEV}" 2>/dev/null || true
rm -rf "${ARCHIVE_HOST}"
mkdir -p "${ARCHIVE_HOST}"

log "=== 34: wipe pipeline (HaMeR/MANO postprocess) ==="
rm -rf "${ROOT}/data-storage/pipeline/${STATION}"

log "=== 34: wipe corpus + annotation publish artifacts ==="
rm -rf "${ROOT}/data-storage/corpus/ego_214_hand_pose"
rm -f "${ROOT}/data-storage/corpus/ego_214_hand_pose.zip"
rm -rf "${ROOT}/data-storage/embodied-annotate/datasets/ego_214"
rm -rf "${ROOT}/data-storage/stream/delivery_samples/ego_dual_v1"
rm -rf "${ROOT}/data-storage/samples/ego_214_hand_pose"*
rm -f "${ROOT}/data-storage/samples/ego_214_hand_pose.zip"*
rm -f "${ROOT}/data-storage/samples/ego_214_hand_pose_hand_kp2d.json"
rm -f "${ROOT}/data-storage/samples/ego_214_hand_pose_depth_preview.json"
rm -rf "${ROOT}/data-storage/samples/ego_214_hand_pose_depth_preview_frames"

log "=== 34: wipe pipeline outputs + published samples ==="
if [[ -d "${PIPE_ROOT}/outputs" ]]; then
  rm -rf "${PIPE_ROOT}/outputs/${STATION}"
  rm -rf "${PIPE_ROOT}/outputs/dataset/${AGGREGATE_NAME}"
  rm -f "${PIPE_ROOT}/outputs/dataset/${SAMPLES_SLUG}.zip"
fi

rm -f "${ROOT}/data-storage/samples/${SAMPLES_SLUG}.zip"
rm -f "${ROOT}/data-storage/samples/${SAMPLES_SLUG}.zip.bak"
rm -f "${ROOT}/data-storage/samples/${SAMPLES_SLUG}_hand_kp2d.json"
rm -f "${ROOT}/data-storage/samples/${SAMPLES_SLUG}.webp"

log "=== Docker: remove bundled copies + restart ingest/lerobot ==="
COMPOSE=(docker compose)
if ! docker compose version &>/dev/null; then
  COMPOSE=(docker-compose)
fi

if docker ps --format '{{.Names}}' | grep -q '^data-lab-lerobot-1$'; then
  docker exec data-lab-lerobot-1 sh -c "
    rm -f /srv/bundled/${SAMPLES_SLUG}.zip \
          /srv/bundled/covers/${SAMPLES_SLUG}.webp \
          /srv/bundled/overlays/${SAMPLES_SLUG}_hand_kp2d.json 2>/dev/null || true
  "
fi

cd "${ROOT}"
"${COMPOSE[@]}" -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml \
  restart stream-ingest derive-worker lerobot nginx >/dev/null 2>&1 || \
"${COMPOSE[@]}" -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml \
  restart stream-ingest lerobot nginx >/dev/null 2>&1 || true

log "=== Verify ==="
frames="$(curl -sf "http://127.0.0.1:8080/lerobot/api/stream/${STATION}/status" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('totalFrames', '?'))" 2>/dev/null || echo "?")"
log "collection API totalFrames=${frames} (expect 0)"
log "done — proceed with runbook stage 1 (capture on 214)"
