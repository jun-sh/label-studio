#!/usr/bin/env bash
# Deploy ego-stream-client intrinsics pipeline to ego-lan-214 ego-studio.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ECS_SRC="${ROOT}/data-lab-platform/ego-stream-client"
EDGE_HOST="${EDGE_HOST:-server@10.10.10.214}"
STUDIO="${EDGE_STUDIO:-/home/server/workspace/ego-studio}"
CAP="${STUDIO}/src/ego_capture_studio/capture"
CLI="${STUDIO}/src/ego_capture_studio/cli"

log() { echo "[deploy-214-intrinsics] $*"; }

log "=== sync intrinsics modules to ${EDGE_HOST} ==="
ssh -o BatchMode=yes "${EDGE_HOST}" "mkdir -p '${CAP}' '${CLI}'"
scp -q \
  "${ECS_SRC}/intrinsics_store.py" \
  "${ECS_SRC}/segment_store.py" \
  "${ECS_SRC}/stream_upload.py" \
  "${EDGE_HOST}:${CAP}/"
scp -q \
  "${ECS_SRC}/record_oak_stream.py" \
  "${EDGE_HOST}:${CAP}/record_oak_stream.py"
scp -q \
  "${ECS_SRC}/record_oak_stream.py" \
  "${ECS_SRC}/cli/ego_upload.py" \
  "${ECS_SRC}/cli/dump_camera_intrinsics.py" \
  "${ECS_SRC}/cli/upload_segments.py" \
  "${EDGE_HOST}:${CLI}/"

log "=== enable EGO_INTRINSICS_STRICT on record-oak-stream ==="
ssh -o BatchMode=yes "${EDGE_HOST}" bash -s <<'REMOTE'
set -euo pipefail
UD="${HOME}/.config/systemd/user"
DROPIN="${UD}/ecs-record-oak-stream.service.d"
mkdir -p "${DROPIN}"
CONF="${DROPIN}/intrinsics-strict.conf"
cat > "${CONF}" <<'EOF'
[Service]
Environment=EGO_INTRINSICS_STRICT=1
EOF
echo "wrote ${CONF}"
systemctl --user daemon-reload
REMOTE

log "=== verify intrinsics_store import ==="
ssh -o BatchMode=yes "${EDGE_HOST}" bash -s <<REMOTE
set -euo pipefail
cd "${STUDIO}"
PYTHONPATH=src python3 -c "
from ego_capture_studio.capture.intrinsics_store import intrinsics_strict_required, backfill_session_intrinsics_if_missing
print('EGO_INTRINSICS_STRICT=', intrinsics_strict_required())
"
REMOTE

log "=== done ==="
log "Ops: dump EEPROM → python3 -m ego_capture_studio.cli.dump_camera_intrinsics --session sess_xxx"
log "Ops: backfill upload → python3 -m ego_capture_studio.cli.ego_upload --ensure-session sess_xxx"
