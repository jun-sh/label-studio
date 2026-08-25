#!/usr/bin/env bash
# Provision ego capture host (130) from this repo: code sync, ffmpeg, systemd, JPEG production path.
# Usage: ego-130-provision.sh [ssh_target] [station_id]
# Example: ego-130-provision.sh server@10.10.10.130 ego-001
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
STATION_ID="${2:-ego-001}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT}/.." && pwd)"
CAPTURE_SRC="${ROOT}/ego-stream-client"
REMOTE_STUDIO="/home/server/workspace/ego-studio"
REMOTE_CONFIG="${REMOTE_STUDIO}/config"
REMOTE_CAPTURE="${REMOTE_STUDIO}/src/ego_capture_studio/capture"
REMOTE_CLI="${REMOTE_STUDIO}/src/ego_capture_studio/cli"
CACHE_ROOT="/home/server/cache/${STATION_ID}"
CAPTURE_HOST="${TARGET#*@}"

echo "==> Provision ${TARGET} station=${STATION_ID}"

if ! ssh -o BatchMode=yes -o ConnectTimeout=8 "${TARGET}" "echo ok" >/dev/null 2>&1; then
  echo "==> SSH key auth unavailable — using paramiko (RC_CAPTURE_PASS)"
  PROVISION_TARGET="${TARGET}" PROVISION_STATION="${STATION_ID}" \
    RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" \
    python3 "${ROOT}/scripts/ego-130-provision-paramiko.py"
  exit $?
fi

ssh "${TARGET}" "mkdir -p ${REMOTE_CAPTURE} ${REMOTE_CLI} ${REMOTE_CONFIG} ${CACHE_ROOT}/segments ${CACHE_ROOT}/logs"

echo "==> Sync capture Python (ego-stream-client -> ego-studio)"
rsync -av --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "${CAPTURE_SRC}/" \
  "${TARGET}:${REMOTE_CAPTURE}/"
for f in camera_map.py topology.py segment_tar_zst.py segment_upload.py upload_status.py; do
  if [[ -f "${CAPTURE_SRC}/${f}" ]]; then
    rsync -av "${CAPTURE_SRC}/${f}" "${TARGET}:${REMOTE_CAPTURE}/${f}"
  fi
done
rsync -av "${CAPTURE_SRC}/cli/record_oak_stream.py" "${TARGET}:${REMOTE_CLI}/record_oak_stream.py"
rsync -av "${CAPTURE_SRC}/cli/upload_segments.py" "${TARGET}:${REMOTE_CLI}/upload_segments.py"
rsync -av "${CAPTURE_SRC}/tools/upload_segments_loop.py" \
  "${TARGET}:${REMOTE_STUDIO}/src/ego_capture_studio/tools/upload_segments_loop.py"
for topo in camera_topology_standard.json camera_topology_standard.yaml; do
  if [[ -f "${CAPTURE_SRC}/config/${topo}" ]]; then
    rsync -av "${CAPTURE_SRC}/config/${topo}" "${TARGET}:${REMOTE_CONFIG}/${topo}"
  fi
done

echo "==> Systemd drop-ins + ffmpeg + JPEG station env"
scp -q "${CAPTURE_SRC}/systemd/ecs-record-oak-stream.service.d/z-production-egoverse.conf" \
  "${TARGET}:/tmp/z-production-egoverse.conf"
scp -q "${CAPTURE_SRC}/systemd/ecs-station-heartbeat.service" \
  "${TARGET}:/tmp/ecs-station-heartbeat.service"
ssh "${TARGET}" bash -s <<REMOTE
set -euo pipefail
echo '1' | sudo -S apt-get install -y ffmpeg rsync 2>/dev/null || sudo apt-get install -y ffmpeg rsync

for d in /etc/systemd/system/ecs-record-oak-stream.service.d \
           "\$HOME/.config/systemd/user/ecs-record-oak-stream.service.d"; do
  echo '1' | sudo -S mkdir -p "\$d" 2>/dev/null || mkdir -p "\$d"
  echo '1' | sudo -S cp /tmp/z-production-egoverse.conf "\$d/z-production-egoverse.conf" 2>/dev/null \
    || cp /tmp/z-production-egoverse.conf "\$d/z-production-egoverse.conf"
  for bad in v0.0.8-segment-mp4.conf ego-standard-production.conf scheme-a.conf \
               v0.0.8-segment-mp4-genpts.conf v0.0.9-h264-plan-b.conf phase2-h264-poc.conf; do
    if [[ -f "\$d/\$bad" ]]; then
      echo '1' | sudo -S mv "\$d/\$bad" "\$d/\$bad.disabled" 2>/dev/null \
        || mv "\$d/\$bad" "\$d/\$bad.disabled"
      echo "disabled \$d/\$bad"
    fi
  done
done

mkdir -p "\$HOME/.config/ego-station.env.d"
cat > "\$HOME/.config/ego-station.env" <<EOF
EGO_STATION_ID=${STATION_ID}
EGO_SEGMENT_ROOT=${CACHE_ROOT}/segments
EGO_EXPORT_ROOT=/home/server/export/${STATION_ID}
EGO_CAPTURE_CHECKPOINT=${CACHE_ROOT}/segments/checkpoint.json
EGO_UPLOAD_LOG_DIR=${CACHE_ROOT}/logs
DATALAB_HEARTBEAT_URL=http://10.10.10.34:8080/lerobot/api/collection/stations/${STATION_ID}/upload
EGO_UPLOAD_URL=http://10.10.10.34:8080/lerobot/api/collection/stations/${STATION_ID}/upload
STATION_UPLOAD_TOKEN=dl-upload-${STATION_ID}-v1
DATALAB_CAPTURE_HOST=${CAPTURE_HOST}
EGO_SEGMENT_DELETE_AFTER_UPLOAD=1
EGO_NOTIFY_PROCESS=1
UPLOAD_PROTOCOL=tarzst
EOF
cat > "\$HOME/.config/ego-station.env.d/station.conf" <<EOF
EGO_SEGMENT_ROOT=${CACHE_ROOT}/segments
EGO_CAPTURE_CHECKPOINT=${CACHE_ROOT}/segments/checkpoint.json
EGO_UPLOAD_URL=http://10.10.10.34:8080/lerobot/api/collection/stations/${STATION_ID}/upload
DATALAB_HEARTBEAT_URL=http://10.10.10.34:8080/lerobot/api/collection/stations/${STATION_ID}/upload
STATION_UPLOAD_TOKEN=dl-upload-${STATION_ID}-v1
DATALAB_CAPTURE_HOST=${CAPTURE_HOST}
EGO_TOPOLOGY_FILE=${REMOTE_CONFIG}/camera_topology_standard.yaml
SEGMENT_FRAME_BIN=1
OAK_HW_JPEG=1
OAK_H264=0
UPLOAD_PROTOCOL=tarzst
EOF
printf 'EGO_UPLOAD_MODE=production\\n' > "\$HOME/.config/ego-station.env.d/upload-mode.conf"
mkdir -p "\$HOME/.config/systemd/user"
cp /tmp/ecs-station-heartbeat.service "\$HOME/.config/systemd/user/ecs-station-heartbeat.service"
LOOP_UNIT=ecs-upload-segments-loop.service
systemctl --user stop "\$LOOP_UNIT" 2>/dev/null || true
systemctl --user disable "\$LOOP_UNIT" 2>/dev/null || true
systemctl --user mask "\$LOOP_UNIT" 2>/dev/null || true
pkill -f '[u]pload_segments_loop.py' 2>/dev/null || true
systemctl --user daemon-reload 2>/dev/null || true
echo '1' | sudo -S systemctl daemon-reload 2>/dev/null || true
systemctl --user enable ecs-station-heartbeat.service 2>/dev/null || true
systemctl --user start ecs-station-heartbeat.service 2>/dev/null || true
REMOTE

# Production upload mode (local script over ssh)
scp -q "${REPO_ROOT}/data-lab-platform/scripts/ego-130-upload-mode.sh" "${TARGET}:/tmp/ego-130-upload-mode.sh"
ssh "${TARGET}" "bash /tmp/ego-130-upload-mode.sh production"

echo "==> Install ego-upload CLI (130 one-liner upload)"
ssh "${TARGET}" "mkdir -p ~/.local/bin"
scp -q "${REPO_ROOT}/data-lab-platform/scripts/ego-upload-station.sh" \
  "${TARGET}:~/.local/bin/ego-upload"
ssh "${TARGET}" "chmod +x ~/.local/bin/ego-upload"
ssh "${TARGET}" "echo '1' | sudo -S cp ~/.local/bin/ego-upload /usr/local/bin/ego-upload"
ssh "${TARGET}" 'grep -q "\.local/bin" ~/.bashrc 2>/dev/null || echo "export PATH=\"\$HOME/.local/bin:\$PATH\"" >> ~/.bashrc'

echo "==> Verify ego-upload supports --notify (P-Ops-3)"
ssh "${TARGET}" "ego-upload --help 2>&1 | grep -q notify" \
  || { echo "FAIL: ego-upload missing --notify"; exit 1; }
echo "OK: ego-upload --notify available"

echo "==> Verify ffmpeg"
ssh "${TARGET}" "which ffmpeg && ffmpeg -version | head -1"

echo "==> Verify JPEG capture path"
ssh "${TARGET}" bash -s <<'VERIFY'
set -euo pipefail
fail=0
die() { echo "FAIL: $*"; fail=1; }
env="$(systemctl --user show ecs-record-oak-stream -p Environment --no-pager 2>/dev/null || true)"
get_env() { echo "$env" | tr ' ' '\n' | grep "^$1=" | cut -d= -f2- || true; }
frame_bin="$(get_env SEGMENT_FRAME_BIN)"
hw_jpeg="$(get_env OAK_HW_JPEG)"
oak_h264="$(get_env OAK_H264)"
[[ "$frame_bin" == "1" ]] || die "SEGMENT_FRAME_BIN=${frame_bin:-unset} (want 1)"
[[ "$hw_jpeg" == "1" ]] || die "OAK_HW_JPEG=${hw_jpeg:-unset} (want 1)"
[[ "$oak_h264" == "0" ]] || die "OAK_H264=${oak_h264:-unset} (want 0)"
grep -q '^OAK_H264=0' "$HOME/.config/ego-station.env.d/station.conf" 2>/dev/null \
  || die "station.conf missing OAK_H264=0"
[[ "$fail" -eq 0 ]] && echo "OK: JPEG-only capture path verified"
exit "$fail"
VERIFY

echo "Done. Start capture: systemctl --user start ecs-record-oak-stream"
echo "Manual upload:     ego-upload ${STATION_ID}"
echo "Upload + notify:   ego-upload ${STATION_ID} --notify   # 需 34 ego-process-watcher timer"
