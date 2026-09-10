#!/usr/bin/env bash
# Record one parallel-drain POC episode on 130; restore production mcap after.
set -euo pipefail

STATION_ID="ego-parallel-drain-poc"
CACHE_ROOT="${HOME}/cache/${STATION_ID}"
SEG_ROOT="${CACHE_ROOT}/segments"
CHECKPOINT="${CACHE_ROOT}/checkpoint.json"
STRICT_EMIT="${CACHE_ROOT}/strict_emit_ts.json"
ACTIVE_ROOT="/tmp/ego-parallel-drain-poc-active"
RECORD_SECONDS="${EGO_STRICT_EPISODE_SECONDS:-15}"
WAIT_BUFFER=20
REMOTE_TARGET=""

_usage() {
  cat <<EOF
Usage: $(basename "$0") [user@host]

  Parallel-drain POC: stop production, record ~${RECORD_SECONDS}s H.264 MCAP, print metrics, restore production.

Options:
  --seconds N          Episode length (default: ${RECORD_SECONDS})
  --no-restart-prod    Leave production stopped after recording
  -h, --help
EOF
}

die() { echo "ego-130-record-parallel-drain-poc: $*" >&2; exit 1; }

RESTART_PROD=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) _usage; exit 0 ;;
    --seconds) RECORD_SECONDS="${2:?}"; shift 2 ;;
    --no-restart-prod) RESTART_PROD=0; shift ;;
    *@*) REMOTE_TARGET="$1"; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

_run_local() {
  local py="${HOME}/workspace/ego-studio/.venv/bin/python"
  [[ -x "$py" ]] || die "missing ego-studio venv: ${py}"
  local record_since
  record_since="$(date '+%Y-%m-%d %H:%M:%S')"

  echo "==> stop conflicting capture units"
  systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-stream.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-mcap.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-mcap-pilot.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-mcap-track2.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-mcap-parallel-drain.service 2>/dev/null || true
  sleep 2

  echo "==> clear parallel-drain checkpoints"
  rm -f "${CHECKPOINT}" "${STRICT_EMIT}"
  mkdir -p "${SEG_ROOT}" "${ACTIVE_ROOT}"

  echo "==> start ecs-record-oak-mcap-parallel-drain.service"
  systemctl --user daemon-reload
  systemctl --user start ecs-record-oak-mcap-parallel-drain.service
  sleep 3
  systemctl --user is-active ecs-record-oak-mcap-parallel-drain.service >/dev/null \
    || die "parallel-drain unit failed (journalctl --user -u ecs-record-oak-mcap-parallel-drain)"

  journalctl --user -u ecs-record-oak-mcap-parallel-drain --since "${record_since}" -n 20 --no-pager \
    | grep -E "parallel_drain|capture_backend|oak_pipeline=" || true

  local pid
  pid="$(pgrep -f 'record_oak_stream_parallel' | head -1 || true)"
  local pidstat_file="/tmp/ego-parallel-drain-pidstat.txt"
  : > "${pidstat_file}"
  if [[ -n "${pid}" ]] && command -v pidstat >/dev/null 2>&1; then
    pidstat -t -p "${pid}" 1 >"${pidstat_file}" &
    local pidstat_bg=$!
  else
    local pidstat_bg=""
  fi

  local wait_s=$((RECORD_SECONDS + WAIT_BUFFER))
  echo "==> recording ${RECORD_SECONDS}s (+${WAIT_BUFFER}s buffer) …"
  local persist_peak=0 capture_fps_peak=0
  local t0
  t0=$(date +%s)
  while [[ $(($(date +%s) - t0)) -lt wait_s ]]; do
    local line pq fps_line fps
    line="$(journalctl --user -u ecs-record-oak-mcap-parallel-drain --since "${record_since}" -n 40 --no-pager 2>/dev/null \
      | grep -oE 'persist_q=[0-9]+' | tail -1 || true)"
    if [[ -n "$line" ]]; then
      pq="${line#persist_q=}"
      if [[ "$pq" -gt "$persist_peak" ]]; then persist_peak="$pq"; fi
    fi
    fps_line="$(journalctl --user -u ecs-record-oak-mcap-parallel-drain --since "${record_since}" -n 40 --no-pager 2>/dev/null \
      | grep -oE 'capture_fps=[0-9.]+' | tail -1 || true)"
    if [[ -n "$fps_line" ]]; then
      fps="${fps_line#capture_fps=}"
      if awk "BEGIN {exit !($fps > $capture_fps_peak)}"; then capture_fps_peak="$fps"; fi
    fi
    sleep 3
  done

  [[ -n "${pidstat_bg}" ]] && kill "${pidstat_bg}" 2>/dev/null || true
  sleep 1

  systemctl --user stop ecs-record-oak-mcap-parallel-drain.service 2>/dev/null || true
  sleep 5

  local done_line capture_fps frame_count dropped
  done_line="$(journalctl --user -u ecs-record-oak-mcap-parallel-drain --since "${record_since}" --no-pager 2>/dev/null \
    | grep 'Done\.' | tail -1 || true)"
  frame_count="$(sed -n 's/.*Done\. \([0-9]*\) frames.*/\1/p' <<< "${done_line}" | tail -1)"
  capture_fps="$(sed -n 's/.*(\([0-9.]*\) fps).*/\1/p' <<< "${done_line}" | tail -1)"
  dropped="$(journalctl --user -u ecs-record-oak-mcap-parallel-drain --since "${record_since}" --no-pager 2>/dev/null \
    | grep -oE 'dropped=[0-9]+' | tail -1 | sed 's/dropped=//' || echo 0)"

  local latest_sid seg_dir mcap_path mcap_bytes
  latest_sid="$(ls -1t "${SEG_ROOT}/sessions" 2>/dev/null | head -1 || true)"
  [[ -n "$latest_sid" ]] || die "no session under ${SEG_ROOT}/sessions"

  seg_dir=""
  for _try in $(seq 1 10); do
    seg_dir="$(find "${SEG_ROOT}/sessions/${latest_sid}" -maxdepth 3 -name segment.mcap -printf '%h\n' 2>/dev/null | head -1 || true)"
    [[ -n "$seg_dir" && -f "${seg_dir}/segment.mcap" ]] && break
    sleep 2
  done
  [[ -n "$seg_dir" && -f "${seg_dir}/segment.mcap" ]] || die "no segment.mcap for session ${latest_sid}"
  mcap_path="${seg_dir}/segment.mcap"
  mcap_bytes="$(stat -c%s "${mcap_path}" 2>/dev/null || echo 0)"

  echo "PARALLEL_DRAIN_POC_RESULT session_id=${latest_sid}"
  echo "PARALLEL_DRAIN_POC_RESULT mcap_path=${mcap_path}"
  echo "PARALLEL_DRAIN_POC_RESULT mcap_mb=$(awk "BEGIN {printf \"%.2f\", ${mcap_bytes}/1048576}")"
  echo "PARALLEL_DRAIN_POC_RESULT frame_count=${frame_count:-0}"
  echo "PARALLEL_DRAIN_POC_RESULT capture_fps_done=${capture_fps:-0}"
  echo "PARALLEL_DRAIN_POC_RESULT capture_fps_peak_log=${capture_fps_peak:-0}"
  echo "PARALLEL_DRAIN_POC_RESULT dropped=${dropped:-0}"
  echo "PARALLEL_DRAIN_POC_RESULT persist_q_peak=${persist_peak}"
  echo "PARALLEL_DRAIN_POC_RESULT done_line=${done_line}"
  echo "PARALLEL_DRAIN_POC_RESULT pid=${pid:-none}"

  if [[ -s "${pidstat_file}" ]]; then
    echo "PARALLEL_DRAIN_POC_RESULT pidstat_top_threads:"
    awk '/ego-cam-drain|ego-imu-drain|record_oak_stream_parallel/ {print}' "${pidstat_file}" | tail -20
    echo "PARALLEL_DRAIN_POC_RESULT pidstat_cpu_summary:"
    awk 'NR>3 && $8 ~ /^[0-9]/ {cpu[$3]+=$8; n[$3]++} END {for (t in cpu) if (n[t]>3) printf "  %s avg_cpu=%.1f%% samples=%d\n", t, cpu[t]/n[t], n[t]}' "${pidstat_file}" \
      | sort -t= -k2 -nr | head -8
  fi

  journalctl --user -u ecs-record-oak-mcap-parallel-drain --since "${record_since}" --no-pager 2>/dev/null \
    | grep -E "capture_fps=|parallel_drain_started|Done\." | tail -15

  if [[ "$RESTART_PROD" -eq 1 ]]; then
    echo "==> restore production ecs-record-oak-mcap.service"
    systemctl --user start ecs-record-oak-mcap.service 2>/dev/null || true
    systemctl --user start ecs-preview-standby.service 2>/dev/null || true
  fi
}

if [[ -n "${REMOTE_TARGET}" ]]; then
  SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  REMOTE_SCRIPT="/tmp/ego-130-record-parallel-drain-poc.sh"
  scp -q "${SCRIPT_PATH}" "${REMOTE_TARGET}:${REMOTE_SCRIPT}" 2>/dev/null || \
    RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" python3 - <<PY
import paramiko, os
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("10.10.10.130", username="server", password=os.environ.get("RC_CAPTURE_PASS","1"), timeout=20)
sftp = c.open_sftp(); sftp.put("${SCRIPT_PATH}", "${REMOTE_SCRIPT}"); sftp.close(); c.close()
PY
  ssh "${REMOTE_TARGET}" "chmod +x ${REMOTE_SCRIPT} && EGO_STRICT_EPISODE_SECONDS=${RECORD_SECONDS} bash ${REMOTE_SCRIPT}" 2>/dev/null || \
    RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" python3 - <<PY
import paramiko, os
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("10.10.10.130", username="server", password=os.environ.get("RC_CAPTURE_PASS","1"), timeout=20)
_, o, e = c.exec_command(f"chmod +x ${REMOTE_SCRIPT} && EGO_STRICT_EPISODE_SECONDS=${RECORD_SECONDS} bash ${REMOTE_SCRIPT}", timeout=600)
print((o.read()+e.read()).decode())
raise SystemExit(o.channel.recv_exit_status())
PY
else
  _run_local
fi
