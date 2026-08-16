#!/usr/bin/env bash
# Provision ego capture host (130) from this repo: code sync, ffmpeg, systemd, production upload mode.
# Usage: ego-130-provision.sh [ssh_target] [station_id]
# Example: ego-130-provision.sh server@10.10.10.130 ego-001
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
STATION_ID="${2:-ego-001}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT}/.." && pwd)"
CAPTURE_SRC="${ROOT}/ego-stream-client"
REMOTE_STUDIO="/home/server/workspace/ego-studio"
REMOTE_CAPTURE="${REMOTE_STUDIO}/src/ego_capture_studio/capture"
REMOTE_CLI="${REMOTE_STUDIO}/src/ego_capture_studio/cli"
CACHE_ROOT="/home/server/cache/${STATION_ID}"

echo "==> Provision ${TARGET} station=${STATION_ID}"

ssh "${TARGET}" "mkdir -p ${REMOTE_CAPTURE} ${REMOTE_CLI} ${CACHE_ROOT}/segments ${CACHE_ROOT}/logs"

echo "==> Sync capture Python (ego-stream-client -> ego-studio)"
rsync -av --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "${CAPTURE_SRC}/" \
  "${TARGET}:${REMOTE_CAPTURE}/"
# Top-level modules that live beside capture/ in the package
for f in camera_map.py topology.py segment_tar_zst.py segment_upload.py upload_status.py; do
  if [[ -f "${CAPTURE_SRC}/${f}" ]]; then
    rsync -av "${CAPTURE_SRC}/${f}" "${TARGET}:${REMOTE_CAPTURE}/${f}"
  fi
done
rsync -av "${CAPTURE_SRC}/cli/record_oak_stream.py" "${TARGET}:${REMOTE_CLI}/record_oak_stream.py"
rsync -av "${CAPTURE_SRC}/cli/upload_segments.py" "${TARGET}:${REMOTE_CLI}/upload_segments.py"
rsync -av "${CAPTURE_SRC}/tools/upload_segments_loop.py" \
  "${TARGET}:${REMOTE_STUDIO}/src/ego_capture_studio/tools/upload_segments_loop.py"

echo "==> Systemd drop-ins + ffmpeg + upload mode"
scp -q "${ROOT}/ego-stream-client/systemd/ecs-record-oak-stream.service.d/v0.0.8-segment-mp4.conf" \
  "${TARGET}:/tmp/v0.0.8-segment-mp4.conf"
ssh "${TARGET}" bash -s <<REMOTE
set -euo pipefail
echo '1' | sudo -S apt-get install -y ffmpeg rsync 2>/dev/null || sudo apt-get install -y ffmpeg rsync

for d in /etc/systemd/system/ecs-record-oak-stream.service.d \
           "\$HOME/.config/systemd/user/ecs-record-oak-stream.service.d"; do
  echo '1' | sudo -S mkdir -p "\$d" 2>/dev/null || mkdir -p "\$d"
  echo '1' | sudo -S cp /tmp/v0.0.8-segment-mp4.conf "\$d/v0.0.8-segment-mp4.conf" 2>/dev/null \
    || cp /tmp/v0.0.8-segment-mp4.conf "\$d/v0.0.8-segment-mp4.conf"
  for bad in z-production-egoverse.conf scheme-a.conf; do
    if [[ -f "\$d/\$bad" ]]; then
      echo '1' | sudo -S mv "\$d/\$bad" "\$d/\$bad.disabled" 2>/dev/null \
        || mv "\$d/\$bad" "\$d/\$bad.disabled"
      echo "disabled \$d/\$bad"
    fi
  done
done

mkdir -p "\$HOME/.config/ego-station.env.d"
cat > "\$HOME/.config/ego-station.env.d/station.conf" <<EOF
EGO_SEGMENT_ROOT=${CACHE_ROOT}/segments
EGO_CAPTURE_CHECKPOINT=${CACHE_ROOT}/segments/checkpoint.json
EGO_UPLOAD_URL=http://10.10.10.34:8080/lerobot/api/collection/stations/${STATION_ID}/upload
SEGMENT_H264=1
SEGMENT_H264_STRICT=1
SEGMENT_H264_MUX_MODE=copy
SEGMENT_H264_MIN_MP4=4
OAK_H264_SEQUENTIAL=1
OAK_H264_BITRATE_KBPS=6000
OAK_H264_KEYFRAME_FREQUENCY=30
OAK_CAM_QUEUE_MAX=32
EGO_SEGMENT_PERSIST_QUEUE_MAX=2048
UPLOAD_PROTOCOL=tarzst
EOF

systemctl --user daemon-reload 2>/dev/null || true
echo '1' | sudo -S systemctl daemon-reload 2>/dev/null || true
REMOTE

# Production upload mode (local script over ssh)
scp -q "${REPO_ROOT}/data-lab-platform/scripts/ego-130-upload-mode.sh" "${TARGET}:/tmp/ego-130-upload-mode.sh"
ssh "${TARGET}" "bash /tmp/ego-130-upload-mode.sh production"

echo "==> Verify ffmpeg"
ssh "${TARGET}" "which ffmpeg && ffmpeg -version | head -1"

echo "==> Verify H264 systemd path"
"${ROOT}/scripts/ego-130-verify-h264.sh" "${TARGET}"

echo ""
echo "Done. Start capture: systemctl --user start ecs-record-oak-stream"
echo "Manual upload: python -m ego_capture_studio.cli.upload_segments --limit 3 --ensure-session"
