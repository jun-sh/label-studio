#!/usr/bin/env bash
# Deploy simple standby preview to ego-001 edge (130): preview_standby on :8765, no ondemand gateway.
# Low power: ego-web starts ecs-preview-standby on browse; stops after idle timeout.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ECS_SRC="${ROOT}/data-lab-platform/ego-stream-client"
EDGE_HOST="${EDGE_HOST:-server@10.10.10.130}"
STUDIO="${EDGE_STUDIO:-/home/server/workspace/ego-studio}"
CAP="${STUDIO}/src/ego_capture_studio/capture"
TOOLS="${STUDIO}/src/ego_capture_studio/tools"

log() { echo "[deploy-130-standby-preview] $*"; }

log "=== sync capture modules ==="
ssh -o BatchMode=yes "${EDGE_HOST}" "mkdir -p '${CAP}' '${TOOLS}'"
rsync -av --exclude '__pycache__' --exclude '*.pyc' \
  "${ECS_SRC}/" "${EDGE_HOST}:${CAP}/"
for f in camera_map.py topology.py segment_tar_zst.py segment_upload.py upload_status.py record_oak_stream.py preview_standby.py capture_state.py; do
  [[ -f "${ECS_SRC}/${f}" ]] && scp -q "${ECS_SRC}/${f}" "${EDGE_HOST}:${CAP}/${f}" || true
done
scp -q "${ECS_SRC}/cli/record_oak_stream.py" "${EDGE_HOST}:${STUDIO}/src/ego_capture_studio/cli/record_oak_stream.py"

log "=== remove ondemand artifacts ==="
ssh -o BatchMode=yes "${EDGE_HOST}" bash -s <<'REMOTE'
set -euo pipefail
UD="${HOME}/.config/systemd/user"
DROPIN="${UD}/ecs-record-oak-stream.service.d"
rm -f "${UD}/ecs-preview-ondemand.service"
rm -f "${UD}/ecs-station-heartbeat.service.d/capture-stack.conf"
rm -f "${HOME}/workspace/ego-studio/src/ego_capture_studio/capture/preview_ondemand.py"
rm -f "${HOME}/workspace/ego-studio/src/ego_capture_studio/cli/preview_ondemand.py"
mkdir -p "${UD}" "${DROPIN}"
REMOTE

log "=== sync systemd units ==="
for unit in \
  ecs-preview-standby.service \
  ecs-oak-standby-stack.target \
  ecs-oak-capture-stack.target \
  ecs-station-heartbeat.service; do
  scp -q "${ECS_SRC}/systemd/${unit}" "${EDGE_HOST}:.config/systemd/user/${unit}"
done
scp -q "${ECS_SRC}/systemd/ecs-station-heartbeat.service.d/zz-always-on.conf" \
  "${EDGE_HOST}:.config/systemd/user/ecs-station-heartbeat.service.d/zz-always-on.conf" 2>/dev/null || true
scp -q "${ECS_SRC}/systemd/ecs-record-oak-stream.service.d/no-standby-preview.conf" \
  "${EDGE_HOST}:.config/systemd/user/ecs-record-oak-stream.service.d/no-standby-preview.conf" 2>/dev/null || true
scp -q "${ECS_SRC}/systemd/ecs-record-oak-stream.service.d/capture-stack.conf" \
  "${EDGE_HOST}:.config/systemd/user/ecs-record-oak-stream.service.d/capture-stack.conf" 2>/dev/null || true
scp -q "${ECS_SRC}/systemd/ecs-record-oak-stream.service.d/v0.0.8-segment-mp4.conf" \
  "${EDGE_HOST}:.config/systemd/user/ecs-record-oak-stream.service.d/v0.0.8-segment-mp4.conf"

log "=== reload + enable heartbeat only (preview on browse via ego-web) ==="
ssh -o BatchMode=yes "${EDGE_HOST}" bash -s <<'REMOTE'
set -euo pipefail
systemctl --user stop ecs-oak-capture-stack.target 2>/dev/null || true
systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
systemctl --user disable ecs-preview-ondemand.service 2>/dev/null || true
systemctl --user stop ecs-preview-ondemand.service 2>/dev/null || true
systemctl --user disable ecs-oak-standby-stack.target 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable ecs-station-heartbeat.service
systemctl --user restart ecs-station-heartbeat.service
sleep 1
echo "--- service status ---"
systemctl --user is-active ecs-station-heartbeat.service || true
systemctl --user is-active ecs-preview-standby.service || true
python3 - <<'PY'
import sys
sys.path.insert(0, "/home/server/workspace/ego-studio/src")
from ego_capture_studio.capture.capture_state import resolve_capture_state
print("capture_state", resolve_capture_state())
PY
REMOTE

log "=== done ==="
