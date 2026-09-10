#!/usr/bin/env bash
# Luxonis checklist on 130: USB speed + 4-cam ISP baseline + ego-like H.264+IMU drain.
# Optional 4th leg: parallel-drain POC journal metrics (full ego host stack).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BENCH_PY_LOCAL="${ROOT}/ego-stream-client/tools/oak_luxonis_bench.py"
POC_SCRIPT_LOCAL="${SCRIPT_DIR}/ego-130-record-parallel-drain-poc.sh"
REMOTE_TARGET=""
DURATION_S="${LUXONIS_BENCH_DURATION_S:-15}"
WITH_POC=1
RESTART_PROD=1
REPORT="/tmp/ego-luxonis-triplet-bench.txt"
REMOTE_BENCH="/tmp/oak_luxonis_bench.py"
REMOTE_POC="/tmp/ego-130-record-parallel-drain-poc.sh"

_usage() {
  cat <<EOF
Usage: $(basename "$0") [user@host] [options]

  Stop capture, run Luxonis triplet bench on 130, print headroom vs 30fps gate.

Options:
  --seconds N       ISP/H264 drain duration (default: ${DURATION_S})
  --no-poc          Skip parallel-drain POC leg (ego host + fsync_quad)
  --no-restart-prod Leave production capture stopped after bench
  -h, --help

Env (forwarded to bench on 130):
  OAK_DEVICE_FPS=30  EGO_CAPTURE_IMU_HZ=200  OAK_H264_BITRATE_KBPS=3000
  OAK_ISP_SCALE_NUM=2  OAK_ISP_SCALE_DEN=3  OAK_CAM_QUEUE_MAX=128

Examples:
  bash $(basename "$0") server@10.10.10.130
  bash $(basename "$0") server@10.10.10.130 --seconds 20 --no-poc
EOF
}

die() { echo "ego-130-luxonis-triplet-bench: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) _usage; exit 0 ;;
    --seconds) DURATION_S="${2:?}"; shift 2 ;;
    --no-poc) WITH_POC=0; shift ;;
    --no-restart-prod) RESTART_PROD=0; shift ;;
    *@*) REMOTE_TARGET="$1"; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

_ssh() {
  if [[ -z "${REMOTE_TARGET}" ]]; then
    bash -s
    return
  fi
  if ssh -o BatchMode=yes -o ConnectTimeout=8 "${REMOTE_TARGET}" "$@"; then
    return 0
  fi
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" TARGET="${REMOTE_TARGET}" REMOTE_CMD="$*" python3 - <<'PY'
import os, paramiko
target = os.environ["TARGET"]
user, _, host = target.partition("@")
cmd = os.environ["REMOTE_CMD"]
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=20)
_, o, e = c.exec_command(cmd, timeout=1800)
out = (o.read() + e.read()).decode()
print(out, end="")
raise SystemExit(o.channel.recv_exit_status())
PY
}

_scp_file() {
  local src="$1" dest="$2"
  if [[ -z "${REMOTE_TARGET}" ]]; then
    install -D -m 755 "${src}" "${dest}"
    return
  fi
  scp -q "${src}" "${REMOTE_TARGET}:${dest}" 2>/dev/null || \
    RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" python3 - <<PY
import paramiko, os
host = "${REMOTE_TARGET#*@}"
user = "${REMOTE_TARGET%@*}"
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS","1"), timeout=20)
sftp = c.open_sftp(); sftp.put("${src}", "${dest}"); sftp.close(); c.close()
PY
}

_print_summary() {
  local report="$1"
  echo "=== SUMMARY (gate: eff_hz >= 29.8, p50 <= 33.5ms) ==="
  awk '
    /LUXONIS_BENCH phase=usb usb_speed=/ { usb=$0 }
    /LUXONIS_BENCH phase=isp summary=/ { isp_min=$0 }
    /LUXONIS_BENCH phase=h264 summary=/ { h264_min=$0 }
    /LUXONIS_BENCH phase=isp stream=CAM_/ { isp_lines[++isp_n]=$0 }
    /LUXONIS_BENCH phase=h264 stream=CAM_/ { h264_lines[++h264_n]=$0 }
    /device_ingest_final/ { poc_ingest=$0 }
    /fsync_quad_final/ { poc_quad=$0 }
    /PARALLEL_DRAIN_POC_RESULT capture_fps_done=/ { poc_fps=$0 }
    END {
      print usb
      print "--- ISP (sensor+USB, no VPU encode) ---"
      for (i=1;i<=isp_n;i++) print isp_lines[i]
      print isp_min
      print "--- H264+IMU (ego-like OAK pipeline) ---"
      for (i=1;i<=h264_n;i++) print h264_lines[i]
      print h264_min
      if (poc_ingest != "" || poc_quad != "") {
        print "--- POC parallel-drain (host+sync) ---"
        if (poc_fps != "") print poc_fps
        if (poc_ingest != "") print poc_ingest
        if (poc_quad != "") print poc_quad
      }
      print ""
      print "Interpretation:"
      print "  ISP min_eff_hz >> H264 min_eff_hz  => VPU encode is the main tax"
      print "  ISP min_eff_hz ~ H264 ~ 26         => USB total bandwidth saturated"
      print "  POC per_cam_ingest < H264 eff_hz   => host/sync added loss (unexpected)"
      print "  usb_speed != SUPER                 => fix USB3 cable/port first"
    }
  ' "${report}" || true
}

_run_local() {
  local py="${HOME}/workspace/ego-studio/.venv/bin/python"
  [[ -x "${py}" ]] || die "missing ego-studio venv: ${py}"
  [[ -f "${REMOTE_BENCH}" ]] || die "missing bench script: ${REMOTE_BENCH}"

  echo "==> stop conflicting capture units"
  systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-stream.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-mcap.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-mcap-pilot.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-mcap-track2.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-mcap-parallel-drain.service 2>/dev/null || true
  sleep 2

  {
    echo "=== ego Luxonis triplet bench ==="
    date -Is
    echo "duration_s=${DURATION_S}"
    echo

    echo "=== [1/3] USB topology + DepthAI usb_speed ==="
    "${py}" "${REMOTE_BENCH}" --mode usb 2>&1 || true
    echo

    echo "=== [2/3] 4-cam ISP drain (no encode, no IMU) @ ${DURATION_S}s ==="
    LUXONIS_BENCH_DURATION_S="${DURATION_S}" \
      OAK_DEVICE_FPS="${OAK_DEVICE_FPS:-30}" \
      OAK_ISP_SCALE_NUM="${OAK_ISP_SCALE_NUM:-2}" \
      OAK_ISP_SCALE_DEN="${OAK_ISP_SCALE_DEN:-3}" \
      "${py}" "${REMOTE_BENCH}" --mode isp --duration "${DURATION_S}" 2>&1 || true
    echo

    echo "=== [3a/4] H.264 + IMU, no FSYNC @ ${DURATION_S}s ==="
    LUXONIS_BENCH_DURATION_S="${DURATION_S}" \
      OAK_DEVICE_FPS="${OAK_DEVICE_FPS:-30}" \
      EGO_CAPTURE_IMU_HZ="${EGO_CAPTURE_IMU_HZ:-200}" \
      OAK_H264_BITRATE_KBPS="${OAK_H264_BITRATE_KBPS:-3000}" \
      OAK_ISP_SCALE_NUM="${OAK_ISP_SCALE_NUM:-2}" \
      OAK_ISP_SCALE_DEN="${OAK_ISP_SCALE_DEN:-3}" \
      OAK_CAM_QUEUE_MAX="${OAK_CAM_QUEUE_MAX:-128}" \
      OAK_GPIO_FSYNC=0 \
      "${py}" "${REMOTE_BENCH}" --mode h264 --duration "${DURATION_S}" 2>&1 || true
    echo

    echo "=== [3b/4] H.264 + IMU + GPIO FSYNC (production-like) @ ${DURATION_S}s ==="
    LUXONIS_BENCH_DURATION_S="${DURATION_S}" \
      OAK_DEVICE_FPS="${OAK_DEVICE_FPS:-30}" \
      EGO_CAPTURE_IMU_HZ="${EGO_CAPTURE_IMU_HZ:-200}" \
      OAK_H264_BITRATE_KBPS="${OAK_H264_BITRATE_KBPS:-3000}" \
      OAK_ISP_SCALE_NUM="${OAK_ISP_SCALE_NUM:-2}" \
      OAK_ISP_SCALE_DEN="${OAK_ISP_SCALE_DEN:-3}" \
      OAK_CAM_QUEUE_MAX="${OAK_CAM_QUEUE_MAX:-128}" \
      OAK_GPIO_FSYNC=1 \
      "${py}" "${REMOTE_BENCH}" --mode h264 --duration "${DURATION_S}" 2>&1 || true
    echo

    if [[ "${WITH_POC}" -eq 1 ]]; then
      echo "=== [4/4] parallel-drain POC (full ego host + fsync_quad) @ ${DURATION_S}s ==="
      if [[ -f "${REMOTE_POC}" ]] && \
         [[ -f "${HOME}/workspace/ego-studio/src/ego_capture_studio/cli/record_oak_stream_parallel.py" ]]; then
        EGO_STRICT_EPISODE_SECONDS="${DURATION_S}" bash "${REMOTE_POC}" --no-restart-prod 2>&1 | \
          grep -E 'PARALLEL_DRAIN_POC_RESULT|device_ingest_final|fsync_quad_final|capture_fps=|oak_pipeline=' || true
      else
        echo "SKIP poc leg: provision parallel-drain first (ego-130-provision-parallel-drain.sh)"
      fi
      echo
    fi
  } | tee "${REPORT}"

  _print_summary "${REPORT}"

  if [[ "${RESTART_PROD}" -eq 1 ]]; then
    echo "==> restore production ecs-record-oak-mcap.service"
    systemctl --user start ecs-record-oak-mcap.service 2>/dev/null || true
    systemctl --user start ecs-preview-standby.service 2>/dev/null || true
  fi

  echo "LUXONIS_TRIPLET_REPORT=${REPORT}"
}

if [[ -n "${REMOTE_TARGET}" ]]; then
  [[ -f "${BENCH_PY_LOCAL}" ]] || die "missing ${BENCH_PY_LOCAL}"
  _scp_file "${BENCH_PY_LOCAL}" "${REMOTE_BENCH}"
  if [[ "${WITH_POC}" -eq 1 && -f "${POC_SCRIPT_LOCAL}" ]]; then
    _scp_file "${POC_SCRIPT_LOCAL}" "${REMOTE_POC}"
  fi
  REMOTE_SELF="/tmp/ego-130-luxonis-triplet-bench.sh"
  _scp_file "${BASH_SOURCE[0]}" "${REMOTE_SELF}"
  _ssh "chmod +x ${REMOTE_SELF} ${REMOTE_BENCH} && \
    LUXONIS_BENCH_DURATION_S=${DURATION_S} \
    WITH_POC=${WITH_POC} \
    RESTART_PROD=${RESTART_PROD} \
    bash ${REMOTE_SELF} --seconds ${DURATION_S} \
    $([[ ${WITH_POC} -eq 0 ]] && echo --no-poc) \
    $([[ ${RESTART_PROD} -eq 0 ]] && echo --no-restart-prod)"
else
  if [[ ! -f "${REMOTE_BENCH}" ]]; then
    [[ -f "${BENCH_PY_LOCAL}" ]] || die "missing bench: ${BENCH_PY_LOCAL} (run from repo or scp to ${REMOTE_BENCH})"
    install -D -m 755 "${BENCH_PY_LOCAL}" "${REMOTE_BENCH}"
  fi
  if [[ "${WITH_POC}" -eq 1 && -f "${POC_SCRIPT_LOCAL}" && ! -f "${REMOTE_POC}" ]]; then
    install -D -m 755 "${POC_SCRIPT_LOCAL}" "${REMOTE_POC}"
  fi
  _run_local
fi
