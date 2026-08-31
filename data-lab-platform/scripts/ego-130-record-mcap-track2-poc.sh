#!/usr/bin/env bash
# Record one Track2 VPU-H.264 MCAP POC episode on 130 (local MCAP only; :7864 upload optional later).
#
#   bash ego-130-record-mcap-track2-poc.sh
#   bash ego-130-record-mcap-track2-poc.sh server@10.10.10.130
set -euo pipefail

STATION_ID="ego-mcap-track2"
CACHE_ROOT="${HOME}/cache/${STATION_ID}"
SEG_ROOT="${CACHE_ROOT}/segments"
CHECKPOINT="${CACHE_ROOT}/checkpoint.json"
STRICT_EMIT="${CACHE_ROOT}/strict_emit_ts.json"
ACTIVE_ROOT="/tmp/ego-mcap-track2-active"
RECORD_SECONDS="${EGO_STRICT_EPISODE_SECONDS:-60}"
WAIT_BUFFER=15
REMOTE_TARGET=""

_usage() {
  cat <<EOF
Usage: $(basename "$0") [user@host]

  Track2 POC: stop pilot, record ~${RECORD_SECONDS}s H.264 MCAP, print metrics, restart pilot.

Options:
  --seconds N     Episode length (default: ${RECORD_SECONDS})
  --no-restart-pilot   Leave pilot stopped after recording
  -h, --help
EOF
}

die() { echo "ego-130-record-mcap-track2-poc: $*" >&2; exit 1; }

RESTART_PILOT=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) _usage; exit 0 ;;
    --seconds) RECORD_SECONDS="${2:?}"; shift 2 ;;
    --no-restart-pilot) RESTART_PILOT=0; shift ;;
    *@*) REMOTE_TARGET="$1"; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

_run_local() {
  local py="${HOME}/workspace/ego-studio/.venv/bin/python"
  [[ -x "$py" ]] || die "missing ego-studio venv: ${py}"
  local record_since
  record_since="$(date '+%Y-%m-%d %H:%M:%S')"

  echo "==> stop conflicting capture (pilot / production / preview)"
  systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-stream.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-mcap-pilot.service 2>/dev/null || true
  systemctl --user stop ecs-record-oak-mcap-track2.service 2>/dev/null || true
  sleep 2

  echo "==> clear track2 checkpoints"
  rm -f "${CHECKPOINT}" "${STRICT_EMIT}"
  mkdir -p "${SEG_ROOT}" "${ACTIVE_ROOT}"

  echo "==> start ecs-record-oak-mcap-track2.service (OAK_HW_PREVIEW_H264 should be 0)"
  systemctl --user daemon-reload
  systemctl --user start ecs-record-oak-mcap-track2.service
  sleep 2
  systemctl --user is-active ecs-record-oak-mcap-track2.service >/dev/null \
    || die "track2 unit failed (journalctl --user -u ecs-record-oak-mcap-track2)"

  local wait_s=$((RECORD_SECONDS + WAIT_BUFFER))
  echo "==> recording ${RECORD_SECONDS}s (+${WAIT_BUFFER}s buffer) …"
  local persist_peak=0
  local t0
  t0=$(date +%s)
  while [[ $(($(date +%s) - t0)) -lt wait_s ]]; do
    local line pq
    line="$(journalctl --user -u ecs-record-oak-mcap-track2 --since "${record_since}" -n 30 --no-pager 2>/dev/null \
      | grep -oE 'persist_q=[0-9]+' | tail -1 || true)"
    if [[ -n "$line" ]]; then
      pq="${line#persist_q=}"
      if [[ "$pq" -gt "$persist_peak" ]]; then persist_peak="$pq"; fi
    fi
    sleep 3
  done

  systemctl --user stop ecs-record-oak-mcap-track2.service 2>/dev/null || true
  sleep 5

  local done_line capture_fps frame_count
  done_line="$(journalctl --user -u ecs-record-oak-mcap-track2 --since "${record_since}" --no-pager 2>/dev/null \
    | grep 'Done\.' | tail -1 || true)"
  frame_count="$(sed -n 's/.*Done\. \([0-9]*\) frames.*/\1/p' <<< "${done_line}" | tail -1)"
  capture_fps="$(sed -n 's/.*(\([0-9.]*\) fps).*/\1/p' <<< "${done_line}" | tail -1)"

  local pq_after
  pq_after="$(journalctl --user -u ecs-record-oak-mcap-track2 --since "${record_since}" -n 50 --no-pager 2>/dev/null \
    | grep -oE 'persist_q=[0-9]+' | tail -1 | sed 's/persist_q=//' || echo 0)"

  local latest_sid seg_dir mcap_path mcap_bytes mcap_summary
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

  echo "==> MCAP summary"
  mcap_summary="$("${py}" - "${mcap_path}" <<'PY' 2>/dev/null || echo '{}'
import json
import sys
from mcap.reader import make_reader

p = sys.argv[1]
topics: dict[str, int] = {}
schema_name = "unknown"
with open(p, "rb") as fp:
    reader = make_reader(fp)
    summary = reader.get_summary()
    schemas = {}
    if summary and summary.schemas:
        schemas = {s.id: s.name for s in summary.schemas.values()}
    for _schema, channel, _message in reader.iter_messages():
        topics[channel.topic] = topics.get(channel.topic, 0) + 1
        if channel.topic.startswith("/ego/camera/") and schema_name == "unknown":
            schema_name = schemas.get(channel.schema_id, schema_name)

cam_topics = {k: v for k, v in topics.items() if k.startswith("/ego/camera/")}
counts = list(cam_topics.values())
parity_ok = len(counts) == 4 and len(set(counts)) == 1 and counts[0] > 0
meta_codec = None
with open(p, "rb") as fp:
    for _schema, channel, message in make_reader(fp).iter_messages():
        if channel.topic == "/ego/session_meta":
            body = json.loads(message.data.decode("utf-8"))
            meta_codec = body.get("video_codec")
            break

print(
    json.dumps(
        {
            "camera_topics": cam_topics,
            "camera_schema": schema_name,
            "video_codec": meta_codec,
            "frames_per_cam": counts[0] if parity_ok else None,
            "parity_4of4": parity_ok,
            "imu_messages": topics.get("/ego/imu/raw", 0),
        }
    )
)
PY
)"

  echo "TRACK2_POC_RESULT session_id=${latest_sid}"
  echo "TRACK2_POC_RESULT mcap_path=${mcap_path}"
  echo "TRACK2_POC_RESULT mcap_bytes=${mcap_bytes}"
  echo "TRACK2_POC_RESULT mcap_mb=$(awk "BEGIN {printf \"%.2f\", ${mcap_bytes}/1048576}")"
  echo "TRACK2_POC_RESULT persist_q_peak=${persist_peak}"
  echo "TRACK2_POC_RESULT persist_q_last_log=${pq_after}"
  echo "TRACK2_POC_RESULT frame_count=${frame_count:-0}"
  echo "TRACK2_POC_RESULT capture_fps=${capture_fps:-0}"
  echo "TRACK2_POC_RESULT done_line=${done_line}"
  echo "TRACK2_POC_RESULT mcap_summary=${mcap_summary}"
  echo "TRACK2_POC_RESULT foxglove_expect=4x_CompressedVideo_h264_1280x800"

  if [[ "$RESTART_PILOT" -eq 1 ]]; then
    echo "==> restart Track1 pilot + preview"
    systemctl --user start ecs-record-oak-mcap-pilot.service 2>/dev/null || true
    systemctl --user start ecs-preview-standby.service 2>/dev/null || true
  fi

  echo "==> Foxglove: open ${mcap_path} — expect 4x CompressedVideo @ 1280x800"
}

if [[ -n "${REMOTE_TARGET}" ]]; then
  SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  REMOTE_SCRIPT="/tmp/ego-130-record-mcap-track2-poc.sh"
  scp "${SCRIPT_PATH}" "${REMOTE_TARGET}:${REMOTE_SCRIPT}"
  ssh "${REMOTE_TARGET}" "chmod +x ${REMOTE_SCRIPT} && EGO_STRICT_EPISODE_SECONDS=${RECORD_SECONDS} bash ${REMOTE_SCRIPT}"
else
  _run_local
fi
