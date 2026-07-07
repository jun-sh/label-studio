#!/usr/bin/env bash
# Deploy remote-preview policy (方案 B) to ego-lan-214 edge host.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ECS_SRC="${ROOT}/data-lab-platform/ego-stream-client"
WEB_SRC="${ROOT}/data-lab-platform/ego-local-web"
EDGE_HOST="${EDGE_HOST:-server@10.10.10.214}"
STUDIO="${EDGE_STUDIO:-/home/server/workspace/ego-studio}"
CAP="${STUDIO}/src/ego_capture_studio/capture"
TOOLS="${STUDIO}/src/ego_capture_studio/tools"
WEB_DEST="${EGO_WEB_DEST:-/home/server/ego-web}"

log() { echo "[deploy-214-preview] $*"; }

log "=== sync capture modules ==="
ssh -o BatchMode=yes "${EDGE_HOST}" "mkdir -p '${CAP}' '${TOOLS}' '${WEB_DEST}'"
scp -q \
  "${ECS_SRC}/capture_state.py" \
  "${ECS_SRC}/preview_standby.py" \
  "${EDGE_HOST}:${CAP}/"
scp -q "${ECS_SRC}/tools/station_heartbeat_loop.py" "${EDGE_HOST}:${TOOLS}/"

log "=== sync ego-web (capture/standby mutual exclusion) ==="
scp -q "${WEB_SRC}/ego_web.py" "${EDGE_HOST}:${WEB_DEST}/ego_web.py"

log "=== sync systemd units ==="
ssh -o BatchMode=yes "${EDGE_HOST}" bash -s <<REMOTE
set -euo pipefail
UD="\${HOME}/.config/systemd/user"
DROPIN="\${UD}/ecs-record-oak-stream.service.d"
mkdir -p "\${UD}" "\${DROPIN}"
REMOTE
for unit in \
  ecs-preview-standby.service \
  ecs-oak-standby-stack.target \
  ecs-oak-capture-stack.target \
  ecs-station-heartbeat.service; do
  scp -q "${ECS_SRC}/systemd/${unit}" "${EDGE_HOST}:.config/systemd/user/${unit}"
done
scp -q "${ECS_SRC}/systemd/ecs-record-oak-stream.service.d/no-standby-preview.conf" \
  "${EDGE_HOST}:.config/systemd/user/ecs-record-oak-stream.service.d/no-standby-preview.conf"

log "=== reload + enable services ==="
ssh -o BatchMode=yes "${EDGE_HOST}" bash -s <<'REMOTE'
set -euo pipefail
systemctl --user stop ecs-oak-capture-stack.target 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable ecs-station-heartbeat.service
systemctl --user restart ecs-station-heartbeat.service
systemctl --user enable ecs-oak-standby-stack.target
systemctl --user start ecs-oak-standby-stack.target
if systemctl --user is-active --quiet ecs-ego-web.service 2>/dev/null; then
  systemctl --user restart ecs-ego-web.service
fi
sleep 3
echo "--- service status ---"
systemctl --user is-active ecs-station-heartbeat.service || true
systemctl --user is-active ecs-preview-standby.service || true
systemctl --user is-active ecs-oak-standby-stack.target || true
echo "--- preview probe ---"
curl -sf --max-time 3 http://127.0.0.1:8765/preview/status || echo "preview_status: pending"
curl -sf --max-time 3 -o /dev/null -w "front_left_jpg:%{http_code}\n" \
  "http://127.0.0.1:8765/preview/front_left/jpg" || echo "front_left_jpg: pending"
echo "--- journal (standby tail) ---"
journalctl --user -u ecs-preview-standby -n 8 --no-pager -o cat 2>/dev/null || true
REMOTE

log "=== done ==="
log "Verify 34: curl -s http://10.10.10.34:8080/lerobot/api/collection/stations/ego-lan-214/ping"
