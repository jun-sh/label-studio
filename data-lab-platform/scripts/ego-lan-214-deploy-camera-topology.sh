#!/usr/bin/env bash
# Deploy ego-standard camera topology to ego-lan-214 (and fleet template).
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
EDGE_HOST="${EDGE_HOST:-server@10.10.10.214}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ECS_SRC="${ROOT}/data-lab-platform/ego-stream-client"
STUDIO="${EDGE_STUDIO:-/home/server/workspace/ego-studio}"
CAP="${STUDIO}/src/ego_capture_studio/capture"
DROPIN="${EDGE_DROPIN:-/home/server/.config/systemd/user/ecs-record-oak-stream.service.d}"

log() { echo "[deploy-camera-topology] $*"; }
die() { echo "[deploy-camera-topology] ERROR: $*" >&2; exit 1; }

log "=== sync topology modules to ${EDGE_HOST} ==="
ssh -o BatchMode=yes "${EDGE_HOST}" "mkdir -p '${CAP}' '${STUDIO}/config' '${DROPIN}'"
scp -q \
  "${ECS_SRC}/topology.py" \
  "${ECS_SRC}/camera_map.py" \
  "${ECS_SRC}/oak_4p_capture.py" \
  "${EDGE_HOST}:${CAP}/"
scp -q \
  "${ECS_SRC}/config/camera_topology_standard.yaml" \
  "${ECS_SRC}/config/camera_topology_standard.json" \
  "${EDGE_HOST}:${STUDIO}/config/"
ssh -o BatchMode=yes "${EDGE_HOST}" "mkdir -p '${CAP}/config'"
scp -q \
  "${ECS_SRC}/config/camera_topology_standard.json" \
  "${EDGE_HOST}:${CAP}/config/camera_topology_standard.json"
scp -q \
  "${ECS_SRC}/systemd/ecs-record-oak-stream.service.d/z-production-egoverse.conf" \
  "${EDGE_HOST}:${DROPIN}/z-production-egoverse.conf"

log "=== reload systemd ==="
ssh -o BatchMode=yes "${EDGE_HOST}" "systemctl --user daemon-reload"

log "=== verify topology load on edge ==="
ssh -o BatchMode=yes "${EDGE_HOST}" bash -s <<REMOTE
set -euo pipefail
cd "${STUDIO}"
export EGO_TOPOLOGY_FILE="${STUDIO}/config/camera_topology_standard.yaml"
PYTHONPATH=src python3 - <<'PY'
from ego_capture_studio.capture.camera_map import (
    ALL_LEROBOT_VIDEO_KEYS,
    OAK_SOCKET_TO_LEROBOT_VIDEO,
    topology_snapshot,
)
snap = topology_snapshot()
assert snap["topology_id"] == "ego-standard", snap
expected = {
    "CAM_A": "observation.images.camera_front_left",
    "CAM_B": "observation.images.camera_depth_left",
    "CAM_C": "observation.images.camera_rear_right",
    "CAM_D": "observation.images.camera_front_right",
}
assert OAK_SOCKET_TO_LEROBOT_VIDEO == expected, OAK_SOCKET_TO_LEROBOT_VIDEO
print("topology_id:", snap["topology_id"])
print("socket_map:", OAK_SOCKET_TO_LEROBOT_VIDEO)
print("video_keys:", ALL_LEROBOT_VIDEO_KEYS)
PY
REMOTE

log "=== restart capture stack (if enabled) ==="
ssh -o BatchMode=yes "${EDGE_HOST}" bash -s <<'REMOTE'
set -euo pipefail
if systemctl --user is-enabled ecs-oak-capture-stack.target >/dev/null 2>&1; then
  systemctl --user restart ecs-record-oak-stream.service || true
  systemctl --user --no-pager --full status ecs-record-oak-stream.service | head -12
else
  echo "ecs-oak-capture-stack.target not enabled — skip restart (topology ready for next start)"
fi
REMOTE

log "=== done ==="
log "Verify after next record: jq .topology sessions/sess_*/meta/camera_intrinsics.json"
