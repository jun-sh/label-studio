#!/usr/bin/env bash
# Fast E2E stress (~5 min wall): 2 short MCAP episodes → upload → derive + L2/L3.
# Product default: L2 passthrough + purity + preview (no WiLoR worker).
# Set EGO_STRESS_SKIP_CONVERT=1 to skip L2/L3 (derive-only SLA check).
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
SLUG="${SAMPLES_SLUG:-ego_001}"
EPISODES="${E2E_EPISODES:-2}"
RECORD_SECONDS="${E2E_RECORD_SECONDS:-45}"
WAIT_AFTER_STOP="${E2E_WAIT_AFTER_STOP:-25}"
POST_READY_SEC="${POST_READY_SEC:-0}"
INTER_EPISODE_SEC="${E2E_INTER_EPISODE_SEC:-15}"
TIMELINE_MAX_RATIO="${EGO_TIMELINE_MAX_RATIO:-1.03}"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"
BUDGET_SEC="${STRESS_BUDGET_SEC:-300}"
PIPELINE_BUDGET_SEC="${STRESS_PIPELINE_BUDGET_SEC:-$(
  if [[ "${EGO_STRESS_SKIP_CONVERT:-0}" == "1" ]]; then echo 420; else echo 600; fi
)}"

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STREAM="${DATALAB}/data-storage/stream/${STATION}"
LOG_DIR="${DATALAB}/data-storage/logs"
REPORT_DIR="${DATALAB}/data-lab-platform/docs/m0-evidence"
mkdir -p "$LOG_DIR" "$REPORT_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_LOG="${LOG_DIR}/stress-5min-${STAMP}.log"
AUDIT_JSON="${REPORT_DIR}/stress-5min-${STAMP}.json"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RUN_LOG"; }
die() { log "FATAL: $*"; exit 1; }

_wipe_stream_dir() {
  local root="$1"
  local attempt
  for attempt in 1 2 3 4 5; do
    [[ -d "$root" ]] || return 0
    find "$root" -mindepth 1 -delete 2>/dev/null || true
    rm -rf "$root" 2>/dev/null || true
    [[ ! -e "$root" ]] && return 0
    sleep 1
  done
  die "failed to wipe stream dir: ${root}"
}

log "=== stress-5min · ${STATION} · ${EPISODES}×${RECORD_SECONDS}s · budget=${BUDGET_SEC}s ==="
log "log=${RUN_LOG}"
RUN_START=$SECONDS

if [[ "${EGO_STRESS_SKIP_CONVERT:-0}" != "1" ]]; then
  log "L2 stress: stop ego-process-watcher + clear in-flight ego-process"
  systemctl stop data-lab-ego-process-watcher.timer 2>/dev/null || true
  systemctl stop data-lab-ego-process-watcher.service 2>/dev/null || true
  pkill -f "ego-process[[:space:]]+${STATION}(\\s|$)" 2>/dev/null || true
  rm -f "${STREAM}/state/process-notify.pending.json" "${STREAM}/state/process-notify.running.json"
  sleep 1
fi

log "=== 0/3 清场 130 segments + 34 stream (before derive-mode) ==="
RC_CAPTURE_PASS="$EDGE_PASS" STATION_ID="$STATION" python3 <<'PY' | tee -a "$RUN_LOG"
import os, paramiko
station = os.environ.get("STATION_ID", "ego-001")
seg = f"/home/server/cache/{station}/segments"
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("10.10.10.130", username="server", password=os.environ.get("RC_CAPTURE_PASS","1"), timeout=20)
script = f"""
set -euo pipefail
systemctl --user stop ecs-record-oak-mcap.service 2>/dev/null || true
systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
sleep 2
pkill -f 'ego_capture_studio|record_oak_stream|preview_standby' 2>/dev/null || true
sleep 2
rm -rf {seg}/sessions {seg}/registry.json {seg}/checkpoint.json {seg}/strict_emit_ts.json
mkdir -p {seg}
echo 130_cleared
"""
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{script}\nREMOTE", timeout=120)
print((o.read()+e.read()).decode().strip())
c.close()
PY

_wipe_stream_dir "$STREAM"
mkdir -p "$STREAM/raw/segments" "$STREAM/state/sessions"
rm -rf "${DATALAB}/data-storage/pipeline/${STATION}"
if [[ "${EGO_STRESS_SKIP_CONVERT:-0}" == "1" ]]; then
  log "34 stream wiped (corpus preserved — derive-only tier)"
else
  log "L2 stress: wipe corpus + samples (${SLUG}) for clean append"
  rm -rf "${DATALAB}/data-storage/corpus/${SLUG}"
  rm -f "${DATALAB}/data-storage/corpus/${SLUG}.zip"
  rm -rf "${DATALAB}/data-storage/samples/${SLUG}"*
  rm -f "${DATALAB}/data-storage/samples/${SLUG}.zip"*
  rm -f "${DATALAB}/data-storage/samples/${SLUG}_hand_kp2d.json"
  rm -f "${DATALAB}/data-storage/samples/${SLUG}_depth_preview.json"
  rm -rf "${DATALAB}/data-storage/samples/${SLUG}_depth_preview_frames"
fi

log ">>> manual derive + legacy gate"
if [[ "${EGO_STRESS_SKIP_DERIVE_MODE:-0}" != "1" ]]; then
  bash "${SCRIPT_DIR}/ego-derive-mode.sh" manual 2>&1 | tee -a "$RUN_LOG"
else
  log "skip derive-mode (EGO_STRESS_SKIP_DERIVE_MODE=1)"
fi

if [[ "${EGO_STRESS_SKIP_PROVISION:-0}" != "1" ]]; then
  log ">>> deploy upload seal fix to 130"
  RC_CAPTURE_PASS="$EDGE_PASS" bash "${SCRIPT_DIR}/ego-130-provision-mcap-production.sh" "server@10.10.10.130" "$STATION" 2>&1 | tee -a "$RUN_LOG"
else
  log "skip 130 provision (EGO_STRESS_SKIP_PROVISION=1)"
fi

log ">>> doctor"
RC_CAPTURE_PASS="$EDGE_PASS" bash "${SCRIPT_DIR}/ego-station-doctor.sh" "$STATION" 2>&1 | tee -a "$RUN_LOG"
doctor_rc=${PIPESTATUS[0]}
[[ "$doctor_rc" -eq 0 ]] || exit "$doctor_rc"

log "=== 1/3 录制+上传 ${EPISODES}×${RECORD_SECONDS}s (beep对齐 POST_READY=${POST_READY_SEC} ep间隔=${INTER_EPISODE_SEC}s) ==="
record_start=$SECONDS
export EGO_UPLOAD_UNTIL_COMPLETE=1
export EGO_UPLOAD_UNTIL_COMPLETE_TIMEOUT_S=420
export EGO_UPLOAD_UNTIL_COMPLETE_POLL_S=1
if [[ "${EGO_STRESS_SKIP_CONVERT:-0}" != "1" ]]; then
  export EGO_E2E_UPLOAD_NOTIFY=0
fi
RC_CAPTURE_PASS="$EDGE_PASS" EPISODES="$EPISODES" RECORD_SECONDS="$RECORD_SECONDS" \
  POST_READY_SEC="$POST_READY_SEC" WAIT_AFTER_STOP="$WAIT_AFTER_STOP" \
  INTER_EPISODE_SEC="$INTER_EPISODE_SEC" \
  python3 -u "${SCRIPT_DIR}/ego-e2e-record-upload.py" 2>&1 | tee -a "$RUN_LOG"
record_rc=${PIPESTATUS[0]}
record_sec=$((SECONDS - record_start))
log "录制+上传 exit=${record_rc} 用时 ${record_sec}s"
[[ "$record_rc" -eq 0 ]] || exit "$record_rc"

done_up=$(find "$STREAM/state/sessions" -name 'session.DONE_UPLOAD' 2>/dev/null | wc -l)
log "DONE_UPLOAD=${done_up}/${EPISODES}"

log "=== 2/3 derive + L2/L3 (product default, no WiLoR) ==="
derive_start=$SECONDS
rm -f "${STREAM}/state/process-notify.pending.json" "${STREAM}/state/process-notify.running.json"
pkill -f "ego-process[[:space:]]+${STATION}(\\s|$)" 2>/dev/null || true
sleep 1
export EGO_USE_CONVERT_WORKER=0
export EGO_OAK_MODE=rectify
export WAIT_PARQUET_SEC=90
export EGO_PARQUET_FAIL_FAST_SEC=60
export EGO_PLATFORM_ROOT="${EGO_PLATFORM_ROOT:-$(cd "${DATALAB}/../ego-platform" && pwd)}"
export PYTHONPATH="${EGO_PLATFORM_ROOT}/src:${PYTHONPATH:-}"
cd "$DATALAB"
if [[ "${EGO_STRESS_SKIP_CONVERT:-0}" == "1" ]]; then
  bash "${SCRIPT_DIR}/ego-process" "$STATION" --force-manual-derive --skip-convert 2>&1 | tee -a "$RUN_LOG"
else
  bash "${SCRIPT_DIR}/ego-process" "$STATION" --force-manual-derive 2>&1 | tee -a "$RUN_LOG"
fi
process_rc=${PIPESTATUS[0]}
derive_sec=$((SECONDS - derive_start))
log "derive exit=${process_rc} 用时 ${derive_sec}s"
[[ "$process_rc" -eq 0 ]] || exit "$process_rc"

ready_n=$(find "$STREAM/state/sessions" -name 'session.READY' 2>/dev/null | wc -l)
failed_n=$(find "$STREAM/state/sessions" -name 'session.FAILED' 2>/dev/null | wc -l)
total_sec=$((SECONDS - RUN_START))
pipeline_sec=$((record_sec + derive_sec))

log "=== 3/3 汇总 ==="
python3 - <<PY | tee -a "$RUN_LOG"
import json
import re
import subprocess
from pathlib import Path

corpus = Path("${DATALAB}/data-storage/corpus/${SLUG}")
mp4s = sorted(
    p
    for p in corpus.glob("videos/**/chunk-*/file-*.mp4")
    if p.is_file() and re.match(r"file-\d+\.mp4$", p.name)
)
corpus_mp4_count = len(mp4s)
corpus_mp4_ok = corpus_mp4_count >= ${EPISODES} * 4
corpus_mp4_dims = []
for mp4 in mp4s[:8]:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x", str(mp4),
        ],
        capture_output=True,
        text=True,
    )
    corpus_mp4_dims.append({"path": mp4.relative_to(corpus).as_posix(), "size": proc.stdout.strip()})

log_path = Path("${RUN_LOG}")
timeline_ratios = []
timeline_retries = 0
pending_ratio = None
for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
    if "重试 episode" in line:
        timeline_retries += 1
    if line.startswith("timeline_ratio "):
        parts = line.split()
        if len(parts) >= 3:
            try:
                pending_ratio = {"segment": parts[1], "ratio": float(parts[2])}
            except ValueError:
                pending_ratio = None
    if pending_ratio and re.search(r"^OK \d+/\d+ upload=", line):
        timeline_ratios.append(pending_ratio)
        pending_ratio = None
timeline_max = max((r["ratio"] for r in timeline_ratios), default=None)
timeline_ok = len(timeline_ratios) >= ${EPISODES} and all(
    abs(r["ratio"] - 1.0) <= (${TIMELINE_MAX_RATIO} - 1.0) + 1e-9 for r in timeline_ratios
)

summary = {
    "station": "${STATION}",
    "episodes_target": ${EPISODES},
    "record_seconds": ${RECORD_SECONDS},
    "post_ready_sec": ${POST_READY_SEC},
    "inter_episode_sec": ${INTER_EPISODE_SEC},
    "done_upload": ${done_up},
    "ready": ${ready_n},
    "failed": ${failed_n},
    "record_upload_sec": ${record_sec},
    "derive_sec": ${derive_sec},
    "pipeline_sec": ${pipeline_sec},
    "total_sec": ${total_sec},
    "pipeline_budget_sec": ${PIPELINE_BUDGET_SEC},
    "timeline_ratios": timeline_ratios,
    "timeline_retries": timeline_retries,
    "timeline_max_ratio": timeline_max,
    "timeline_max_dev": ${TIMELINE_MAX_RATIO} - 1.0,
    "timeline_ok": timeline_ok,
    "corpus_mp4_count": corpus_mp4_count,
    "corpus_mp4_expected": ${EPISODES} * 4,
    "corpus_mp4_ok": corpus_mp4_ok,
    "corpus_mp4_dims": corpus_mp4_dims,
    "passed": ${ready_n} >= ${EPISODES} and ${failed_n} == 0 and ${pipeline_sec} <= ${PIPELINE_BUDGET_SEC} and ${done_up} >= ${EPISODES} and corpus_mp4_ok and timeline_ok,
}
print(json.dumps(summary, indent=2))
Path("${AUDIT_JSON}").write_text(json.dumps(summary, indent=2) + "\n")
PY

log "READY=${ready_n} FAILED=${failed_n} pipeline=${pipeline_sec}s total=${total_sec}s budget=${PIPELINE_BUDGET_SEC}s"
log "audit=${AUDIT_JSON}"

corpus_mp4_n=$(find "${DATALAB}/data-storage/corpus/${SLUG}/videos" -name 'file-*.mp4' 2>/dev/null | grep -E 'file-[0-9]+\.mp4$' | wc -l)
log "corpus_mp4=${corpus_mp4_n}/$((EPISODES * 4))"

if [[ "$ready_n" -ge "$EPISODES" && "$failed_n" -eq 0 && "$pipeline_sec" -le "$PIPELINE_BUDGET_SEC" && "$done_up" -ge "$EPISODES" && "$corpus_mp4_n" -ge $((EPISODES * 4)) ]]; then
  audit_passed="$(python3 -c "import json; print(json.load(open('${AUDIT_JSON}')).get('passed', False))")"
  if [[ "$audit_passed" == "True" ]]; then
    log "🎉 stress-5min PASS"
    systemctl start data-lab-ego-process-watcher.timer 2>/dev/null || true
    exit 0
  fi
  log "⚠️ stress-5min pipeline OK but audit failed (timeline_ok or other gate)"
fi
log "⚠️ stress-5min 未达标"
systemctl start data-lab-ego-process-watcher.timer 2>/dev/null || true
exit 1
