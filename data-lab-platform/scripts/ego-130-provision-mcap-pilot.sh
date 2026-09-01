#!/usr/bin/env bash
# Provision ego-mcap-pilot capture on 130 (isolated from ego-001 production path).
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
STATION_ID="ego-mcap-pilot"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CAPTURE_SRC="${ROOT}/ego-stream-client"
REMOTE_STUDIO="/home/server/workspace/ego-studio"
REMOTE_CAPTURE="${REMOTE_STUDIO}/src/ego_capture_studio/capture"
REMOTE_CLI="${REMOTE_STUDIO}/src/ego_capture_studio/cli"
REMOTE_SYSTEMD="/home/server/.config/systemd/user"
CACHE_ROOT="/home/server/cache/${STATION_ID}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Provision MCAP pilot ${TARGET} station=${STATION_ID}"

ssh "${TARGET}" "mkdir -p ${REMOTE_CAPTURE} ${REMOTE_CLI} ${CACHE_ROOT}/segments /tmp/ego-mcap-pilot-active"

echo "==> Sync capture code"
rsync -av --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "${CAPTURE_SRC}/" \
  "${TARGET}:${REMOTE_CAPTURE}/"
rsync -av "${CAPTURE_SRC}/cli/record_oak_stream.py" "${TARGET}:${REMOTE_CLI}/record_oak_stream.py"
rsync -av "${CAPTURE_SRC}/cli/upload_segments.py" "${TARGET}:${REMOTE_CLI}/upload_segments.py"

echo "==> Install pilot systemd unit (does not modify ecs-record-oak-stream)"
ssh "${TARGET}" "mkdir -p ${REMOTE_SYSTEMD}/ecs-record-oak-mcap-pilot.service.d"
rsync -av "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap-pilot.service" \
  "${TARGET}:${REMOTE_SYSTEMD}/ecs-record-oak-mcap-pilot.service"
rsync -av "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap-pilot.service.d/z-mcap-pilot.conf" \
  "${TARGET}:${REMOTE_SYSTEMD}/ecs-record-oak-mcap-pilot.service.d/z-mcap-pilot.conf"

echo "==> Install ego-upload wrapper"
rsync -av "${SCRIPT_DIR}/ego-upload-station.sh" "${TARGET}:/tmp/ego-upload-station.sh"
ssh "${TARGET}" "mkdir -p ~/.local/bin && cp /tmp/ego-upload-station.sh ~/.local/bin/ego-upload && chmod +x ~/.local/bin/ego-upload"

ssh "${TARGET}" "systemctl --user daemon-reload"
echo "==> Pilot unit installed (not started). Record + upload with:"
echo "    bash data-lab-platform/scripts/ego-130-record-mcap-pilot.sh ${TARGET} --notify"
echo "==> Or manually:"
echo "    ssh ${TARGET} 'systemctl --user start ecs-record-oak-mcap-pilot.service'"
echo "==> Upload (pilot :7863, not production :8080):"
echo "    ego-upload ${STATION_ID} --notify"
