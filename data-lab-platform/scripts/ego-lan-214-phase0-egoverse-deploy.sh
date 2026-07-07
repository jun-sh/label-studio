#!/usr/bin/env bash
# Phase0: purge legacy data + deploy EgoVerse 30/200 on 214 + wipe 34 stream.
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
EDGE_HOST="${EDGE_HOST:-server@10.10.10.214}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ECS_SRC="${ROOT}/data-lab-platform/ego-stream-client"
CAP="${EDGE_STUDIO:-/home/server/workspace/ego-studio}/src/ego_capture_studio/capture"
CLI="${EDGE_STUDIO:-/home/server/workspace/ego-studio}/src/ego_capture_studio/cli"
DROPIN="/home/server/.config/systemd/user/ecs-record-oak-stream.service.d"

log() { echo "[phase0-egoverse] $*"; }
die() { echo "[phase0-egoverse] ERROR: $*" >&2; exit 1; }

if [[ "${EGO_RESET_YES:-}" != "1" ]]; then
  read -r -p "Purge ALL ${STATION} data and deploy 30/200? [y/N] " ans
  [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]] || die "aborted"
fi

log "=== 34: full reset (stream + 214 segments + pipeline) ==="
EGO_RESET_YES=1 bash "${ROOT}/data-lab-platform/scripts/ego-lan-214-reset-for-rerun.sh" --yes

log "=== 214: stop + purge ==="
ssh -o BatchMode=yes "${EDGE_HOST}" bash -s <<'REMOTE'
set -euo pipefail
systemctl --user stop ecs-oak-capture-stack.target ecs-oak-upload-stack.target 2>/dev/null || true
rm -rf /home/server/cache/ego-lan-214/segments/*
rm -rf /home/server/export/ego-lan-214/*
rm -rf /dev/shm/ego-capture-active/*
rm -f  /home/server/cache/ego-lan-214/segments/checkpoint.json
rm -f  /home/server/cache/ego-lan-214/segments/strict_emit_ts.json
rm -f  ~/.config/systemd/user/ecs-record-oak-stream.service.d/z-strict-20hz.conf*
df -h /
REMOTE

log "=== 214: sync capture modules ==="
ssh -o BatchMode=yes "${EDGE_HOST}" "mkdir -p ${CAP} ${CLI} ${DROPIN}"
scp -q "${ECS_SRC}/oak_4p_capture.py" "${ECS_SRC}/segment_store.py" "${ECS_SRC}/camera_map.py" \
    "${ECS_SRC}/stream_upload.py" "${ECS_SRC}/record_oak_stream.py" \
    "${EDGE_HOST}:${CAP}/"
scp -q "${ECS_SRC}/cli/record_oak_stream.py" "${EDGE_HOST}:${CLI}/record_oak_stream.py"
scp -q "${ECS_SRC}/ego_spec_capture_patch.py" "${EDGE_HOST}:${CAP}/ego_spec_capture_patch.py"
scp -q "${ECS_SRC}/systemd/ecs-record-oak-stream.service.d/z-production-egoverse.conf" \
    "${EDGE_HOST}:${DROPIN}/z-production-egoverse.conf"

log "=== 214: patch ego_spec + systemd ==="
ssh -o BatchMode=yes "${EDGE_HOST}" bash -s <<'REMOTE'
set -euo pipefail
STUDIO="$HOME/workspace/ego-studio"
for spec in "$STUDIO/src/ego_capture_studio/capture/ego_spec.py" \
            "$STUDIO/src/ego_capture_studio/capture/ego_spec_capture_patch.py"; do
  [[ -f "$spec" ]] || continue
  sed -i 's/OAK_CAPTURE_FPS = 20/OAK_CAPTURE_FPS = 30/g' "$spec"
  sed -i 's/OAK_CAPTURE_IMU_HZ = 100/OAK_CAPTURE_IMU_HZ = 200/g' "$spec"
done
rm -f "$HOME/.config/systemd/user/ecs-record-oak-stream.service.d/z-strict-20hz.conf"*
systemctl --user daemon-reload
systemctl --user show ecs-record-oak-stream.service -p ExecStart --no-pager | grep -q -- '--fps 30' \
  || { systemctl --user cat ecs-record-oak-stream.service | tail -30; exit 1; }
echo "systemd: 30/200 OK"
lsusb | grep -i 03e7 || echo "WARN: no OAK USB"
REMOTE

log "=== Phase0 deploy complete ==="
log "Next: ssh ${EDGE_HOST} 'systemctl --user start ecs-oak-capture-stack.target'"
log "      journalctl --user -u ecs-record-oak-stream -f"
