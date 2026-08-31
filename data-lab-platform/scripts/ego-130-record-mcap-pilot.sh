#!/usr/bin/env bash
# Record one MCAP pilot episode on 130 and upload to 34 :7863 (field-test SOP).
#
# Run on 130 (after ego-130-provision-mcap-pilot.sh):
#   bash ego-130-record-mcap-pilot.sh --notify
#
# From 34 via SSH:
#   bash data-lab-platform/scripts/ego-130-record-mcap-pilot.sh server@10.10.10.130 --notify
set -euo pipefail

STATION_ID="ego-mcap-pilot"
CACHE_ROOT="${HOME}/cache/${STATION_ID}"
SEG_ROOT="${CACHE_ROOT}/segments"
CHECKPOINT="${CACHE_ROOT}/checkpoint.json"
STRICT_EMIT="${CACHE_ROOT}/strict_emit_ts.json"
ACTIVE_ROOT="/tmp/ego-mcap-pilot-active"
UPLOAD_URL="http://10.10.10.34:7863/lerobot/api/collection/stations/${STATION_ID}/upload"
RECORD_SECONDS="${EGO_STRICT_EPISODE_SECONDS:-35}"
WAIT_BUFFER=8
DO_UPLOAD=1
NOTIFY_FLAG=""
USE_SYSTEMD=1
STOP_PREVIEW=1
REMOTE_TARGET=""

_usage() {
  cat <<EOF
Usage: $(basename "$0") [user@host] [options]

  Record one ego-mcap-pilot MCAP episode on 130 and upload to :7863.

Options:
  --seconds N       Strict episode length (default: ${RECORD_SECONDS})
  --notify          Pass --notify to ego-upload (queue ego-process on 34)
  --no-upload       Record only; skip upload
  --manual          Run record_oak_stream in foreground (default: systemd unit)
  --no-stop-preview Leave ecs-preview-standby running (not recommended)
  -h, --help        Show this help

After upload on 34:
  ego-process ${STATION_ID}
EOF
}

die() { echo "ego-130-record-mcap-pilot: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) _usage; exit 0 ;;
    --seconds)
      RECORD_SECONDS="${2:?--seconds requires value}"
      shift 2
      ;;
    --notify) NOTIFY_FLAG="--notify"; shift ;;
    --no-upload) DO_UPLOAD=0; shift ;;
    --manual) USE_SYSTEMD=0; shift ;;
    --no-stop-preview) STOP_PREVIEW=0; shift ;;
    *@*) REMOTE_TARGET="$1"; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

_run_local() {
  local py="${HOME}/workspace/ego-studio/.venv/bin/python"
  [[ -x "$py" ]] || die "missing ego-studio venv: ${py}"

  if [[ "$STOP_PREVIEW" -eq 1 ]]; then
    echo "==> stop OAK preview / conflicting capture"
    systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
    systemctl --user stop ecs-record-oak-stream.service 2>/dev/null || true
    systemctl --user stop ecs-record-oak-mcap-pilot.service 2>/dev/null || true
    sleep 2
  fi

  echo "==> clear pilot checkpoints"
  rm -f "${CHECKPOINT}" "${STRICT_EMIT}" "${SEG_ROOT}/checkpoint.json" "${SEG_ROOT}/strict_emit_ts.json"
  mkdir -p "${SEG_ROOT}" "${ACTIVE_ROOT}"

  if [[ "$USE_SYSTEMD" -eq 1 ]]; then
    echo "==> start ecs-record-oak-mcap-pilot.service"
    systemctl --user daemon-reload
    systemctl --user start ecs-record-oak-mcap-pilot.service
    sleep 2
    systemctl --user is-active ecs-record-oak-mcap-pilot.service >/dev/null \
      || die "mcap pilot unit failed to start (journalctl --user -u ecs-record-oak-mcap-pilot)"
  else
    echo "==> manual record (foreground)"
    export PYTHONPATH="${HOME}/workspace/ego-studio/src"
    export EGO_STATION_ID="${STATION_ID}"
    export EGO_SEGMENT_ROOT="${SEG_ROOT}"
    export EGO_SEGMENT_ACTIVE_ROOT="${ACTIVE_ROOT}"
    export EGO_CAPTURE_CHECKPOINT="${CHECKPOINT}"
    export SEGMENT_MCAP=1 SEGMENT_FRAME_BIN=0 UPLOAD_PROTOCOL=mcap
    export OAK_HW_JPEG=1 OAK_H264=0 SEGMENT_H264=0 EGO_CAPTURE_JPEG_ONLY=1
    export EGO_FRAME_INTERVAL_MS=33 EGO_CAPTURE_FPS=30 EGO_CAPTURE_IMU_HZ=200
    export OAK_DEVICE_FPS=30 OAK_GPIO_FSYNC=1 EGO_CAPTURE_NO_HEARTBEAT=1
    export EGO_SEGMENT_MAX_SECONDS=75 EGO_SEGMENT_MAX_FRAMES=1800
    export EGO_STRICT_EPISODE_SECONDS="${RECORD_SECONDS}"
    "${py}" -m ego_capture_studio.cli.record_oak_stream \
      --fps 30 --imu-hz 200 \
      --segment-root "${SEG_ROOT}" \
      --checkpoint-path "${CHECKPOINT}" &
    RECORD_PID=$!
  fi

  local wait_s=$((RECORD_SECONDS + WAIT_BUFFER))
  echo "==> recording ${RECORD_SECONDS}s (+${WAIT_BUFFER}s buffer) …"
  sleep "${wait_s}"

  if [[ "$USE_SYSTEMD" -eq 1 ]]; then
    systemctl --user stop ecs-record-oak-mcap-pilot.service 2>/dev/null || true
  else
    kill "${RECORD_PID}" 2>/dev/null || true
    wait "${RECORD_PID}" 2>/dev/null || true
  fi

  if [[ "$STOP_PREVIEW" -eq 1 ]]; then
    systemctl --user start ecs-preview-standby.service 2>/dev/null || true
  fi

  local latest_sid
  latest_sid="$(ls -1t "${SEG_ROOT}/sessions" 2>/dev/null | head -1 || true)"
  [[ -n "$latest_sid" ]] || die "no session dir under ${SEG_ROOT}/sessions"
  echo "==> session=${latest_sid}"

  if [[ "$DO_UPLOAD" -eq 0 ]]; then
    echo "==> skip upload (--no-upload)"
    echo "    ego-upload ${STATION_ID} --session-id ${latest_sid} ${NOTIFY_FLAG}"
    return 0
  fi

  echo "==> upload to ${UPLOAD_URL}"
  export PATH="${HOME}/.local/bin:${PATH}"
  command -v ego-upload >/dev/null 2>&1 || die "ego-upload not in PATH (run ego-130-provision-mcap-pilot.sh first)"
  # shellcheck disable=SC2086
  ego-upload "${STATION_ID}" --session-id "${latest_sid}" ${NOTIFY_FLAG}

  echo "==> done — on 34 run: ego-process ${STATION_ID}"
}

chmod_self() {
  chmod +x "$0" 2>/dev/null || true
}

if [[ -n "${REMOTE_TARGET}" ]]; then
  SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  REMOTE_SCRIPT="/tmp/ego-130-record-mcap-pilot.sh"
  scp "${SCRIPT_PATH}" "${REMOTE_TARGET}:${REMOTE_SCRIPT}"
  ssh "${REMOTE_TARGET}" "chmod +x ${REMOTE_SCRIPT} && EGO_STRICT_EPISODE_SECONDS=${RECORD_SECONDS} bash ${REMOTE_SCRIPT}"
else
  _run_local
fi
