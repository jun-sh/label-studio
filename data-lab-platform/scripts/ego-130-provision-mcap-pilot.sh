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
echo "==> Upload (pilot :7863, not production :8080):"
echo "    export EGO_UPLOAD_URL=http://10.10.10.34:7863/lerobot/api/collection/stations/${STATION_ID}/upload"
echo "    export EGO_SEGMENT_ROOT=/home/server/cache/${STATION_ID}/segments"
echo "    UPLOAD_PROTOCOL=mcap STATION_UPLOAD_TOKEN=dl-upload-ego-mcap-pilot-v1 ego-upload ${STATION_ID} --notify"
echo "==> Short test record: export EGO_STRICT_EPISODE_SECONDS=35; rm -f checkpoint.json strict_emit_ts.json"
