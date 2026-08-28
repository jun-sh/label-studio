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
REMOTE_SYSTEMD="${HOME}/.config/systemd/user"
CACHE_ROOT="/home/server/cache/${STATION_ID}"

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

ssh "${TARGET}" "systemctl --user daemon-reload"
echo "==> Pilot unit installed (not started). Start with:"
echo "    ssh ${TARGET} 'systemctl --user start ecs-record-oak-mcap-pilot.service'"
echo "==> Upload with: UPLOAD_PROTOCOL=mcap ego-upload ${STATION_ID}"
