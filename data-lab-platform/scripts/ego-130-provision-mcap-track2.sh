#!/usr/bin/env bash
# Provision ego-mcap-track2 VPU-H.264 POC capture on 130 (isolated from pilot / ego-001).
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
STATION_ID="ego-mcap-track2"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CAPTURE_SRC="${ROOT}/ego-stream-client"
REMOTE_STUDIO="/home/server/workspace/ego-studio"
REMOTE_CAPTURE="${REMOTE_STUDIO}/src/ego_capture_studio/capture"
REMOTE_CLI="${REMOTE_STUDIO}/src/ego_capture_studio/cli"
REMOTE_SYSTEMD="/home/server/.config/systemd/user"
CACHE_ROOT="/home/server/cache/${STATION_ID}"

echo "==> Provision MCAP Track2 H.264 POC ${TARGET} station=${STATION_ID}"

ssh "${TARGET}" "mkdir -p ${REMOTE_CAPTURE} ${REMOTE_CLI} ${CACHE_ROOT}/segments /tmp/ego-mcap-track2-active"

echo "==> Sync capture code"
rsync -av --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "${CAPTURE_SRC}/" \
  "${TARGET}:${REMOTE_CAPTURE}/"
rsync -av "${CAPTURE_SRC}/cli/record_oak_stream.py" "${TARGET}:${REMOTE_CLI}/record_oak_stream.py"

echo "==> Install track2 systemd unit (conflicts with mcap-pilot)"
ssh "${TARGET}" "mkdir -p ${REMOTE_SYSTEMD}/ecs-record-oak-mcap-track2.service.d"
rsync -av "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap-track2.service" \
  "${TARGET}:${REMOTE_SYSTEMD}/ecs-record-oak-mcap-track2.service"
rsync -av "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap-track2.service.d/z-mcap-track2-h264.conf" \
  "${TARGET}:${REMOTE_SYSTEMD}/ecs-record-oak-mcap-track2.service.d/z-mcap-track2-h264.conf"

ssh "${TARGET}" "systemctl --user daemon-reload"
echo "==> Track2 unit installed (not started). Record with:"
echo "    bash data-lab-platform/scripts/ego-130-record-mcap-track2-poc.sh ${TARGET}"
