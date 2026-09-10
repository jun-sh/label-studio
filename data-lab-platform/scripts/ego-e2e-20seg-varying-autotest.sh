#!/usr/bin/env bash
# E2E: 20 MCAP episodes (30-60s varying) → upload → async derive → ego-process + perf metrics.
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
EPISODES="${E2E_EPISODES:-20}"
MIN_SEC="${E2E_MIN_SECONDS:-30}"
MAX_SEC="${E2E_MAX_SECONDS:-60}"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"
WAIT_AFTER_STOP="${E2E_WAIT_AFTER_STOP:-20}"
PIPELINE_READY_TIMEOUT="${PIPELINE_READY_TIMEOUT:-120}"
POST_READY_SEC="${POST_READY_SEC:-8}"

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STREAM="${DATALAB}/data-storage/stream/${STATION}"
LOG_DIR="${DATALAB}/data-storage/logs"
mkdir -p "$LOG_DIR"
RUN_LOG="${LOG_DIR}/e2e-20seg-$(date +%Y%m%d-%H%M%S).log"
METRICS_JSON="${LOG_DIR}/e2e-20seg-metrics-$(date +%Y%m%d-%H%M%S).json"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RUN_LOG"; }
metrics_line() { echo "$1" >> "$METRICS_JSON"; }

TOTAL_START=$SECONDS
metrics_line "{"
metrics_line "  \"station\": \"${STATION}\","
metrics_line "  \"episodes_target\": ${EPISODES},"
metrics_line "  \"duration_range_sec\": [${MIN_SEC}, ${MAX_SEC}],"
metrics_line "  \"started_at\": \"$(date -Iseconds)\","
metrics_line "  \"episodes\": ["

log "=== E2E 20段自动测试 · ${STATION} · ${MIN_SEC}-${MAX_SEC}s/段 ==="
log "日志: ${RUN_LOG}"
log "指标: ${METRICS_JSON}"

log "=== 0/4 预检 130 OAK ==="
RC_CAPTURE_PASS="$EDGE_PASS" python3 - <<'PY' | tee -a "$RUN_LOG"
import os, paramiko, sys
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("10.10.10.130", username="server", password=os.environ.get("RC_CAPTURE_PASS","1"), timeout=20)
_, o, e = c.exec_command("lsusb | grep -i 03e7 || true", timeout=15)
usb = (o.read()+e.read()).decode().strip()
if "f63c" in usb.lower():
    print("OAK in bootloader — booting…")
    _, o, e = c.exec_command("cd ~/workspace/ego-studio && .venv/bin/python -m ego_capture_studio.tools.oak_boot_from_bootloader", timeout=90)
    print((o.read()+e.read()).decode().strip())
    _, o, e = c.exec_command("sleep 3; lsusb | grep -i 03e7", timeout=15)
    usb = (o.read()+e.read()).decode().strip()
if not usb:
    print("FAIL: no OAK USB device"); sys.exit(1)
print(f"OAK OK: {usb.splitlines()[-1]}")
c.close()
PY

log "=== 1/4 自动录制+上传 ${EPISODES} 段 (30-60s 随机) ==="
record_start=$SECONDS
RC_CAPTURE_PASS="$EDGE_PASS" EPISODES="$EPISODES" MIN_SEC="$MIN_SEC" MAX_SEC="$MAX_SEC" \
  WAIT_AFTER_STOP="$WAIT_AFTER_STOP" PIPELINE_READY_TIMEOUT="$PIPELINE_READY_TIMEOUT" \
  POST_READY_SEC="$POST_READY_SEC" METRICS_JSON="$METRICS_JSON" RUN_LOG="$RUN_LOG" \
  python3 -u "${SCRIPT_DIR}/ego-e2e-20seg-record-upload.py" 2>&1 | tee -a "$RUN_LOG"
record_rc=${PIPESTATUS[0]}
record_sec=$((SECONDS - record_start))
log "录制+上传阶段 exit=${record_rc} 用时 ${record_sec}s"
if [[ "$record_rc" -ne 0 ]]; then
  metrics_line "  ],"
  metrics_line "  \"record_upload_sec\": ${record_sec},"
  metrics_line "  \"failed\": true"
  metrics_line "}"
  exit "$record_rc"
fi

raw_n=$(find "$STREAM/raw/segments" -mindepth 1 -maxdepth 1 -type d -name 'sess_*' 2>/dev/null | wc -l)
log "=== 2/4 等待 async derive READY (${raw_n}/${EPISODES}) ==="
log "说明: ego-upload 上传后 derive-worker 自动派生（无需手动 derive-start）"
derive_wait_start=$SECONDS
deadline=$((SECONDS + ${E2E_DERIVE_DEADLINE_SEC:-7200}))
last_ready=0
while (( SECONDS < deadline )); do
  ready_n=$(find "$STREAM/state/sessions" -name 'session.READY' 2>/dev/null | wc -l)
  failed_n=$(find "$STREAM/state/sessions" -name 'session.FAILED' 2>/dev/null | wc -l)
  derive_json=$(curl -sf "http://127.0.0.1:8080/lerobot/api/collection/stations/${STATION}/derive-status" 2>/dev/null || echo '{}')
  phase=$(echo "$derive_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('phase','?'))" 2>/dev/null || echo "?")
  if [[ "$ready_n" != "$last_ready" ]]; then
    log "derive progress: ready=${ready_n}/${raw_n} failed=${failed_n} phase=${phase} elapsed=$((SECONDS - derive_wait_start))s"
    last_ready=$ready_n
  fi
  if [[ "$raw_n" -ge "$EPISODES" && "$ready_n" -ge "$EPISODES" ]]; then
    break
  fi
  if [[ "$failed_n" -gt 0 ]]; then
    log "WARN: ${failed_n} session(s) FAILED — listing:"
    find "$STREAM/state/sessions" -name 'session.FAILED' 2>/dev/null | tee -a "$RUN_LOG"
  fi
  sleep 10
done
derive_wait_sec=$((SECONDS - derive_wait_start))
ready_n=$(find "$STREAM/state/sessions" -name 'session.READY' 2>/dev/null | wc -l)
failed_n=$(find "$STREAM/state/sessions" -name 'session.FAILED' 2>/dev/null | wc -l)
log "derive 完成: ready=${ready_n}/${raw_n} failed=${failed_n} 用时 ${derive_wait_sec}s"
if [[ "$ready_n" -lt "$EPISODES" ]]; then
  log "FAIL: derive 未全部 READY"
  exit 1
fi

log "=== 3/4 ego-process (convert + viewer + export) ==="
# shellcheck source=ego-pipeline-performance.env.sh
source "${SCRIPT_DIR}/ego-pipeline-performance.env.sh"
export EGO_USE_CONVERT_WORKER=0
export EGO_EXPORT_REQUIRE_QC=0
export EGO_EXPORT_REQUIRE_ANNOTATION=0
process_start=$SECONDS
cd "$DATALAB"
bash "${SCRIPT_DIR}/ego-process" "$STATION" 2>&1 | tee -a "$RUN_LOG"
process_rc=${PIPESTATUS[0]}
process_sec=$((SECONDS - process_start))
log "ego-process exit=${process_rc} 用时 ${process_sec}s"
if [[ "$process_rc" -ne 0 ]]; then
  exit "$process_rc"
fi

total_sec=$((SECONDS - TOTAL_START))
corpus_eps=$(python3 -c "
import json
from pathlib import Path
p=Path('${DATALAB}/data-storage/corpus/egodome/meta/info.json')
print(json.loads(p.read_text()).get('total_episodes',0) if p.is_file() else 0)
")
manifests=$(find "${DATALAB}/data-storage/ego-delivery" -path '*/episodes/*/meta/export_manifest.json' 2>/dev/null | wc -l)

log "=== 4/4 验收 ==="
log "uploaded=${raw_n} derive_ready=${ready_n} corpus_episodes=${corpus_eps} manifests=${manifests}"
log "耗时: record+upload=${record_sec}s derive_wait=${derive_wait_sec}s ego-process=${process_sec}s total=${total_sec}s"
log "Collection: http://10.10.10.34:8080/collection?station=${STATION}"
log "Egodome:    http://10.10.10.34:8080/data/egodome"
log "日志: ${RUN_LOG}"

python3 - <<PY
import json
from pathlib import Path
m = Path("${METRICS_JSON}")
# close episodes array and add summary
text = m.read_text().rstrip()
if text.endswith(","):
    text = text[:-1]
if not text.endswith("]"):
    text += "\n  ]"
summary = {
    "record_upload_sec": ${record_sec},
    "derive_wait_sec": ${derive_wait_sec},
    "ego_process_sec": ${process_sec},
    "total_sec": ${total_sec},
    "uploaded": ${raw_n},
    "derive_ready": ${ready_n},
    "derive_failed": ${failed_n},
    "corpus_episodes": ${corpus_eps},
    "manifests": ${manifests},
    "passed": ${corpus_eps} >= ${EPISODES} and ${manifests} >= ${EPISODES},
}
out = text + ",\n" + json.dumps(summary, indent=2)[1:-1] + "\n}\n"
m.write_text(out)
print(json.dumps(summary, indent=2))
PY

if [[ "$corpus_eps" -ge "$EPISODES" ]]; then
  log "🎉 E2E 全流程通过"
else
  log "⚠️ corpus/manifest 未完全达标"
  exit 1
fi
