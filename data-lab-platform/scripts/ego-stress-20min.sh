#!/usr/bin/env bash
# Stress E2E (Tier-3 long soak): total recording ≤20min, pilot profile.
# Interpret FAIL via QA tiers: capture gate = hard corrupt; timeline 3% = upload/corpus;
# stats 5% = ego-soak-timeline.sh reporting only (not daily gate).
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
SLUG="${SAMPLES_SLUG:-ego_001}"
# 14 episodes · 980s recorded (~16.3 min) — under 20min budget
EPISODES="${E2E_EPISODES:-14}"
DURATIONS_CSV="${STRESS_DURATIONS_CSV:-45,45,50,50,55,60,60,65,70,75,75,100,100,120}"
RECORD_BUDGET_SEC="${STRESS_RECORD_BUDGET_SEC:-1200}"
PIPELINE_BUDGET_SEC="${STRESS_PIPELINE_BUDGET_SEC:-7200}"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"
WAIT_AFTER_STOP="${E2E_WAIT_AFTER_STOP:-22}"
POST_READY_SEC="${POST_READY_SEC:-5}"

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STREAM="${DATALAB}/data-storage/stream/${STATION}"
LOG_DIR="${DATALAB}/data-storage/logs"
REPORT_DIR="${DATALAB}/data-lab-platform/docs/m0-evidence"
mkdir -p "$LOG_DIR" "$REPORT_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_LOG="${LOG_DIR}/stress-20min-${STAMP}.log"
AUDIT_JSON="${REPORT_DIR}/stress-20min-${STAMP}.json"
METRICS_JSON="${LOG_DIR}/stress-20min-metrics-${STAMP}.json"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RUN_LOG"; }

_record_sum() {
  python3 - <<PY
csv = "${DURATIONS_CSV}"
print(sum(int(x) for x in csv.split(",") if x.strip()))
PY
}
RECORD_SUM="$(_record_sum)"
if (( RECORD_SUM > RECORD_BUDGET_SEC )); then
  echo "FAIL: DURATIONS_CSV sum=${RECORD_SUM}s > budget=${RECORD_BUDGET_SEC}s" >&2
  exit 2
fi

log "=== stress-20min · ${STATION} · ${EPISODES} ep · record_sum=${RECORD_SUM}s / budget=${RECORD_BUDGET_SEC}s ==="
log "durations=${DURATIONS_CSV}"
log "log=${RUN_LOG}"

RUN_START=$SECONDS

if [[ "${EGO_STRESS_SKIP_DERIVE_MODE:-0}" != "1" ]]; then
  bash "${SCRIPT_DIR}/ego-derive-mode.sh" manual 2>&1 | tee -a "$RUN_LOG"
fi

if [[ "${EGO_STRESS_SKIP_PROVISION:-0}" != "1" ]]; then
  unset EGO_STRESS_CAPTURE
  RC_CAPTURE_PASS="$EDGE_PASS" CAPTURE_PROFILE=pilot \
    bash "${SCRIPT_DIR}/ego-130-provision-mcap-production.sh" "server@10.10.10.130" "$STATION" 2>&1 | tee -a "$RUN_LOG"
fi

RC_CAPTURE_PASS="$EDGE_PASS" bash "${SCRIPT_DIR}/ego-release-check.sh" "$STATION" 2>&1 | tee -a "$RUN_LOG"

log "=== 0/4 清场 130 + 34 stream ==="
RC_CAPTURE_PASS="$EDGE_PASS" python3 <<'PY' | tee -a "$RUN_LOG"
import os, paramiko
station = "ego-001"
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
systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
echo 130_cleared
"""
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{script}\nREMOTE", timeout=120)
print((o.read()+e.read()).decode().strip())
c.close()
PY

rm -rf "$STREAM"
mkdir -p "$STREAM/raw/segments" "$STREAM/state/sessions"
rm -rf "${DATALAB}/data-storage/pipeline/${STATION}"
rm -rf "${DATALAB}/data-storage/corpus/${SLUG}" "${DATALAB}/data-storage/corpus/${SLUG}.zip"
rm -rf "${DATALAB}/data-storage/samples/${SLUG}"*
docker exec data-lab-lerobot-1 sh -c "
  rm -rf /srv/bundled/${SLUG} /srv/bundled/${SLUG}.zip \
    /srv/bundled/covers/${SLUG}.webp \
    /srv/bundled/overlays/${SLUG}_* 2>/dev/null || true
" 2>/dev/null || true

log "=== 1/4 录制+上传 ${EPISODES} ep (pilot durations) ==="
record_start=$SECONDS
echo "{" > "$METRICS_JSON"
echo "  \"episodes\": [" >> "$METRICS_JSON"
RC_CAPTURE_PASS="$EDGE_PASS" EPISODES="$EPISODES" DURATIONS_CSV="$DURATIONS_CSV" \
  MIN_SEC=40 MAX_SEC=120 POST_READY_SEC="$POST_READY_SEC" WAIT_AFTER_STOP="$WAIT_AFTER_STOP" \
  METRICS_JSON="$METRICS_JSON" RUN_LOG="$RUN_LOG" \
  python3 -u "${SCRIPT_DIR}/ego-e2e-20seg-record-upload.py" 2>&1 | tee -a "$RUN_LOG"
record_rc=${PIPESTATUS[0]}
record_sec=$((SECONDS - record_start))
echo "  ]," >> "$METRICS_JSON"
echo "  \"record_upload_sec\": ${record_sec}" >> "$METRICS_JSON"
echo "}" >> "$METRICS_JSON"
log "record+upload rc=${record_rc} sec=${record_sec}"
[[ "$record_rc" -eq 0 ]] || exit "$record_rc"

done_up=$(find "$STREAM/state/sessions" -name 'session.DONE_UPLOAD' 2>/dev/null | wc -l)
raw_n=$(find "$STREAM/raw/segments" -mindepth 1 -maxdepth 1 -type d -name 'sess_*' 2>/dev/null | wc -l)

log "=== 2/4 derive + convert (worker=1, stride=3) ==="
export EGO_USE_CONVERT_WORKER=1
export EGO_EXPORT_REQUIRE_QC=0
export EGO_EXPORT_REQUIRE_ANNOTATION=0
process_start=$SECONDS
cd "$DATALAB"
bash "${SCRIPT_DIR}/ego-process" "$STATION" --force-manual-derive 2>&1 | tee -a "$RUN_LOG"
process_rc=${PIPESTATUS[0]}
process_sec=$((SECONDS - process_start))
log "ego-process rc=${process_rc} sec=${process_sec}"
[[ "$process_rc" -eq 0 ]] || exit "$process_rc"

ready_n=$(find "$STREAM/state/sessions" -name 'session.READY' 2>/dev/null | wc -l)
failed_n=$(find "$STREAM/state/sessions" -name 'session.FAILED' 2>/dev/null | wc -l)
total_sec=$((SECONDS - RUN_START))
pipeline_sec=$((record_sec + process_sec))

corpus_eps=0
corpus_frames=0
if [[ -f "${DATALAB}/data-storage/corpus/${SLUG}/meta/info.json" ]]; then
  read -r corpus_eps corpus_frames <<< "$(python3 -c "
import json
from pathlib import Path
m=json.loads(Path('${DATALAB}/data-storage/corpus/${SLUG}/meta/info.json').read_text())
print(m.get('total_episodes',0), m.get('total_frames',0))
")"
fi

PROC_LOG=$(ls -t "${LOG_DIR}/ego-process-${STATION}-"*.log 2>/dev/null | head -1)
nal_n=0
if [[ -n "$PROC_LOG" ]]; then
  nal_n=$(grep -c "Invalid NAL unit size" "$PROC_LOG" 2>/dev/null || true)
  nal_n=${nal_n:-0}
fi

log "=== 3/4 audit ==="
mapfile -t SESSION_IDS < <(find "$STREAM/raw/segments" -mindepth 1 -maxdepth 1 -type d -name 'sess_*' -printf '%f\n' 2>/dev/null | sort)
AUDIT_ARGS=()
for sid in "${SESSION_IDS[@]}"; do
  AUDIT_ARGS+=(--session-id "$sid")
done
python3 "${SCRIPT_DIR}/ego-pipeline-e2e-audit.py" --slug "$SLUG" --station "$STATION" \
  "${AUDIT_ARGS[@]}" --report "$AUDIT_JSON" 2>&1 | tee -a "$RUN_LOG" || true

log "=== 4/4 summary ==="
python3 - <<PY | tee -a "$RUN_LOG"
import json
from pathlib import Path
summary = {
    "station": "${STATION}",
    "episodes_target": ${EPISODES},
    "record_sum_sec": ${RECORD_SUM},
    "record_budget_sec": ${RECORD_BUDGET_SEC},
    "uploaded": ${raw_n},
    "done_upload": ${done_up},
    "ready": ${ready_n},
    "failed": ${failed_n},
    "corpus_episodes": ${corpus_eps},
    "corpus_frames": ${corpus_frames},
    "nal_errors": ${nal_n},
    "record_upload_sec": ${record_sec},
    "process_sec": ${process_sec},
    "pipeline_sec": ${pipeline_sec},
    "total_sec": ${total_sec},
    "pipeline_budget_sec": ${PIPELINE_BUDGET_SEC},
    "passed": (
        ${ready_n} >= ${EPISODES}
        and ${failed_n} == 0
        and ${done_up} >= ${EPISODES}
        and ${corpus_eps} >= ${EPISODES}
        and ${nal_n} == 0
        and ${pipeline_sec} <= ${PIPELINE_BUDGET_SEC}
    ),
}
print(json.dumps(summary, indent=2))
Path("${AUDIT_JSON}".replace(".json", "-summary.json")).write_text(json.dumps(summary, indent=2) + "\\n")
PY

log "READY=${ready_n}/${EPISODES} corpus=${corpus_eps} NAL=${nal_n} pipeline=${pipeline_sec}s total=${total_sec}s"
log "audit=${AUDIT_JSON}"

if [[ "$ready_n" -ge "$EPISODES" && "$failed_n" -eq 0 && "$done_up" -ge "$EPISODES" && "$corpus_eps" -ge "$EPISODES" && "$nal_n" -eq 0 && "$pipeline_sec" -le "$PIPELINE_BUDGET_SEC" ]]; then
  log "🎉 stress-20min PASS"
  exit 0
fi
log "⚠️ stress-20min 未达标"
exit 1
