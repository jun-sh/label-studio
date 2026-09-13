#!/usr/bin/env bash
# Stress E2E: wipe 130+34 → 10 MCAP episodes (20–500s random) → upload → manual derive → ego-process.
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
SLUG="${SAMPLES_SLUG:-ego_001}"
EPISODES="${E2E_EPISODES:-10}"
MIN_SEC="${E2E_MIN_SECONDS:-20}"
MAX_SEC="${E2E_MAX_SECONDS:-500}"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"
POST_READY_SEC="${POST_READY_SEC:-0}"
WAIT_AFTER_STOP="${E2E_WAIT_AFTER_STOP:-25}"
E2E_DERIVE_DEADLINE_SEC="${E2E_DERIVE_DEADLINE_SEC:-14400}"

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STREAM="${DATALAB}/data-storage/stream/${STATION}"
LOG_DIR="${DATALAB}/data-storage/logs"
REPORT_DIR="${DATALAB}/data-lab-platform/docs/m0-evidence"
mkdir -p "$LOG_DIR" "$REPORT_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_LOG="${LOG_DIR}/stress-10seg-${STAMP}.log"
METRICS_JSON="${LOG_DIR}/stress-10seg-metrics-${STAMP}.json"
AUDIT_JSON="${REPORT_DIR}/stress-10seg-${STAMP}.json"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RUN_LOG"; }

log "=== stress-10seg · ${STATION} · ${EPISODES} ep · ${MIN_SEC}-${MAX_SEC}s ==="
log "log=${RUN_LOG}"

log ">>> ensure manual derive + legacy gate"
bash "${SCRIPT_DIR}/ego-derive-mode.sh" manual 2>&1 | tee -a "$RUN_LOG"

log "=== 0/5 全量清场 130 + 34 (含 raw/corpus/viewer) ==="
export EGO_STRESS_CAPTURE=1
export CAPTURE_PROFILE=long
RC_CAPTURE_PASS="$EDGE_PASS" bash "${SCRIPT_DIR}/ego-130-provision-mcap-production.sh" "server@10.10.10.130" "${STATION}" 2>&1 | tee -a "$RUN_LOG" || true

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
systemctl --user stop ecs-record-oak-stream.service 2>/dev/null || true
sleep 2
pkill -f 'ego_capture_studio|record_oak_stream|preview_standby' 2>/dev/null || true
sleep 2
rm -rf {seg}/sessions {seg}/registry.json {seg}/checkpoint.json {seg}/strict_emit_ts.json
rm -f /home/server/cache/{station}/checkpoint.json /home/server/cache/{station}/strict_emit_ts.json
mkdir -p {seg}
rm -rf /home/server/export/{station} /tmp/{station}-mcap-active 2>/dev/null || true
systemctl --user start ecs-preview-standby.service 2>/dev/null || true
echo 130_cleared
"""
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{script}\nREMOTE", timeout=180)
print((o.read()+e.read()).decode().strip())
c.close()
PY

rm -rf "$STREAM"
mkdir -p "$STREAM/raw/segments" "$STREAM/state/sessions"
rm -rf "${DATALAB}/data-storage/pipeline/${STATION}"
rm -rf "${DATALAB}/data-storage/corpus/${SLUG}" "${DATALAB}/data-storage/corpus/${SLUG}.zip"
rm -rf "${DATALAB}/data-storage/samples/${SLUG}"*
rm -rf "${DATALAB}/data-storage/ego-delivery/${STATION}"
docker exec data-lab-lerobot-1 sh -c "
  rm -rf /srv/bundled/${SLUG} /srv/bundled/${SLUG}.zip \
    /srv/bundled/covers/${SLUG}.webp \
    /srv/bundled/overlays/${SLUG}_* 2>/dev/null || true
" 2>/dev/null || true
log "34 stream wiped; corpus/samples cleared"

log "=== 1/5 预检 OAK ==="
RC_CAPTURE_PASS="$EDGE_PASS" python3 - <<'PY' | tee -a "$RUN_LOG"
import os, paramiko, sys
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("10.10.10.130", username="server", password=os.environ.get("RC_CAPTURE_PASS","1"), timeout=20)
_, o, e = c.exec_command("lsusb | grep -i 03e7 || true", timeout=15)
usb = (o.read()+e.read()).decode().strip()
if "f63c" in usb.lower():
    print("OAK bootloader — booting…")
    _, o, e = c.exec_command("cd ~/workspace/ego-studio && .venv/bin/python -m ego_capture_studio.tools.oak_boot_from_bootloader", timeout=90)
    print((o.read()+e.read()).decode().strip())
if not usb:
    print("FAIL: no OAK"); sys.exit(1)
print(f"OAK OK: {usb.splitlines()[-1]}")
c.close()
PY

metrics_line() { echo "$1" >> "$METRICS_JSON"; }
metrics_line "{"
metrics_line "  \"station\": \"${STATION}\","
metrics_line "  \"slug\": \"${SLUG}\","
metrics_line "  \"episodes_target\": ${EPISODES},"
metrics_line "  \"duration_range_sec\": [${MIN_SEC}, ${MAX_SEC}],"
metrics_line "  \"started_at\": \"$(date -Iseconds)\","
metrics_line "  \"episodes\": ["

log "=== 2/5 录制+上传 ${EPISODES} 段 (${MIN_SEC}-${MAX_SEC}s 随机) ==="
record_start=$SECONDS
RC_CAPTURE_PASS="$EDGE_PASS" EPISODES="$EPISODES" MIN_SEC="$MIN_SEC" MAX_SEC="$MAX_SEC" \
  POST_READY_SEC="$POST_READY_SEC" WAIT_AFTER_STOP="$WAIT_AFTER_STOP" \
  METRICS_JSON="$METRICS_JSON" RUN_LOG="$RUN_LOG" \
  python3 -u "${SCRIPT_DIR}/ego-e2e-20seg-record-upload.py" 2>&1 | tee -a "$RUN_LOG"
record_rc=${PIPESTATUS[0]}
record_sec=$((SECONDS - record_start))
log "录制+上传 exit=${record_rc} 用时 ${record_sec}s"
[[ "$record_rc" -eq 0 ]] || exit "$record_rc"

raw_n=$(find "$STREAM/raw/segments" -mindepth 1 -maxdepth 1 -type d -name 'sess_*' 2>/dev/null | wc -l)
done_up=$(find "$STREAM/state/sessions" -name 'session.DONE_UPLOAD' 2>/dev/null | wc -l)
log "raw_sessions=${raw_n} DONE_UPLOAD=${done_up}"

log "=== 3/5 manual derive + convert (ego-process --force-manual-derive) ==="
# Pilot SOP: WiLoR convert worker warm (Aug-18 HaMeR used per-session cold start ~28s/90f).
export EGO_USE_CONVERT_WORKER=1
export EGO_EXPORT_REQUIRE_QC=0
export EGO_EXPORT_REQUIRE_ANNOTATION=0
derive_start=$SECONDS
cd "$DATALAB"
bash "${SCRIPT_DIR}/ego-process" "$STATION" --force-manual-derive 2>&1 | tee -a "$RUN_LOG"
process_rc=${PIPESTATUS[0]}
process_sec=$((SECONDS - derive_start))
log "ego-process exit=${process_rc} 用时 ${process_sec}s"
[[ "$process_rc" -eq 0 ]] || exit "$process_rc"

ready_n=$(find "$STREAM/state/sessions" -name 'session.READY' 2>/dev/null | wc -l)
failed_n=$(find "$STREAM/state/sessions" -name 'session.FAILED' 2>/dev/null | wc -l)
quar_n=$(find "$STREAM/state/sessions" -name 'session.QUARANTINED' 2>/dev/null | wc -l)

log "=== 4/5 pipeline audit ==="
mapfile -t SESSION_IDS < <(find "$STREAM/raw/segments" -mindepth 1 -maxdepth 1 -type d -name 'sess_*' -printf '%f\n' 2>/dev/null | sort)
AUDIT_ARGS=()
for sid in "${SESSION_IDS[@]}"; do
  AUDIT_ARGS+=(--session-id "$sid")
done
python3 "${SCRIPT_DIR}/ego-pipeline-e2e-audit.py" --slug "$SLUG" --station "$STATION" \
  "${AUDIT_ARGS[@]}" --report "$AUDIT_JSON" 2>&1 | tee -a "$RUN_LOG" || true

log "=== 5/5 验收汇总 ==="
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
total_sec=$((SECONDS))
log "uploaded=${raw_n} ready=${ready_n} failed=${failed_n} quarantined=${quar_n}"
log "corpus episodes=${corpus_eps} frames=${corpus_frames}"
log "耗时: record+upload=${record_sec}s process=${process_sec}s total=${total_sec}s"
log "Collection: http://10.10.10.34:8080/collection?station=${STATION}"
log "Viewer:     http://10.10.10.34:8080/data/${SLUG}"
log "audit:      ${AUDIT_JSON}"
log "log:        ${RUN_LOG}"

python3 - <<PY | tee -a "$RUN_LOG"
import json
from pathlib import Path
summary = {
    "uploaded": ${raw_n},
    "ready": ${ready_n},
    "failed": ${failed_n},
    "quarantined": ${quar_n},
    "corpus_episodes": ${corpus_eps},
    "corpus_frames": ${corpus_frames},
    "record_upload_sec": ${record_sec},
    "ego_process_sec": ${process_sec},
    "total_sec": ${total_sec},
    "passed": ${ready_n} >= ${EPISODES} and ${corpus_eps} >= ${EPISODES},
}
print(json.dumps(summary, indent=2))
Path("${METRICS_JSON}".replace(".json", "-summary.json")).write_text(json.dumps(summary, indent=2)+"\n")
PY

if [[ "$ready_n" -ge "$EPISODES" && "$corpus_eps" -ge "$EPISODES" ]]; then
  log "🎉 stress-10seg 全流程通过"
else
  log "⚠️ 未完全达标 — 见 FAILED/quarantine 与 audit"
  exit 1
fi
