#!/usr/bin/env bash
# Full E2E: wipe 130+34 → record 10 MCAP episodes → derive READY → ego-process (P1 perf).
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
EPISODES="${E2E_EPISODES:-10}"
RECORD_SECONDS="${E2E_RECORD_SECONDS:-50}"
WAIT_AFTER_STOP="${E2E_WAIT_AFTER_STOP:-20}"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STREAM="${DATALAB}/data-storage/stream/${STATION}"
LOG_DIR="${DATALAB}/data-storage/logs"
mkdir -p "$LOG_DIR"
RUN_LOG="${LOG_DIR}/e2e-full-autotest-$(date +%Y%m%d-%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RUN_LOG"; }

log "=== 1/4 清场 130 + 34 ==="
docker stop data-lab-derive-worker-1 2>/dev/null || true
RC_CAPTURE_PASS="$EDGE_PASS" python3 <<'PY' | tee -a "$RUN_LOG"
import os, paramiko
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("10.10.10.130", username="server", password=os.environ.get("RC_CAPTURE_PASS","1"), timeout=20)
station = "ego-001"
seg = f"/home/server/cache/{station}/segments"
script = f"""
set -euo pipefail
systemctl --user stop ecs-record-oak-mcap.service 2>/dev/null || true
systemctl --user stop ecs-record-oak-stream.service 2>/dev/null || true
systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
sleep 2
pkill -f 'ego_capture_studio|record_oak_stream|preview_standby' 2>/dev/null || true
sleep 2
systemctl --user reset-failed ecs-record-oak-mcap.service 2>/dev/null || true
rm -rf {seg}/sessions {seg}/registry.json {seg}/checkpoint.json {seg}/strict_emit_ts.json
rm -f /home/server/cache/{station}/checkpoint.json /home/server/cache/{station}/strict_emit_ts.json
mkdir -p {seg}
rm -rf /home/server/export/{station} /tmp/ego-001-mcap-active 2>/dev/null || true
# ego-001 MCAP H.264: station.conf must not override systemd drop-in
if grep -q '^OAK_H264=0' ~/.config/ego-station.env.d/station.conf 2>/dev/null; then
  cp ~/.config/ego-station.env.d/station.conf ~/.config/ego-station.env.d/station.conf.bak-e2e-$(date +%Y%m%d-%H%M%S)
  sed -i 's/^OAK_HW_JPEG=1/OAK_HW_JPEG=0/; s/^OAK_H264=0/OAK_H264=1/; s/^SEGMENT_FRAME_BIN=1/SEGMENT_FRAME_BIN=0/; s/^UPLOAD_PROTOCOL=tarzst/UPLOAD_PROTOCOL=mcap/' ~/.config/ego-station.env.d/station.conf
  echo station_conf_patched
fi
if lsusb | grep -q '03e7:f63c'; then
  cd ~/workspace/ego-studio && .venv/bin/python -m ego_capture_studio.tools.oak_boot_from_bootloader || true
fi
systemctl --user daemon-reload
echo "130 cleared"
"""
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{script}\nREMOTE", timeout=180)
print((o.read() + e.read()).decode().strip())
c.close()
PY

rm -rf "$STREAM"
mkdir -p "$STREAM/raw/segments" "$STREAM/state/sessions"
rm -rf "${DATALAB}/data-storage/pipeline/${STATION}"
rm -rf "${DATALAB}/data-storage/corpus/egodome" "${DATALAB}/data-storage/corpus/egodome.zip"
rm -rf "${DATALAB}/data-storage/samples/egodome"*
rm -rf "${DATALAB}/data-storage/ego-delivery/${STATION}"
mkdir -p "${DATALAB}/data-storage/ego-delivery/${STATION}"
docker exec data-lab-lerobot-1 sh -c 'rm -rf /srv/bundled/egodome /srv/bundled/egodome.zip /srv/bundled/covers/egodome.webp /srv/bundled/overlays/egodome_* 2>/dev/null || true' 2>/dev/null || true
docker start data-lab-derive-worker-1 2>/dev/null || true
log "34 raw=$(find "$STREAM/raw/segments" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l) (expect 0)"

log "=== 2/4 自动录制+上传 ${EPISODES} 段 (130) ==="
RC_CAPTURE_PASS="$EDGE_PASS" EPISODES="$EPISODES" RECORD_SECONDS="$RECORD_SECONDS" WAIT_AFTER_STOP="$WAIT_AFTER_STOP" \
  python3 -u "${SCRIPT_DIR}/ego-e2e-record-upload.py" 2>&1 | tee -a "$RUN_LOG"

log "=== 3/4 等待 derive READY ${EPISODES}/${EPISODES} ==="
derive_wait_start=$SECONDS
deadline=$((SECONDS + 2400))
while (( SECONDS < deadline )); do
  raw_n=$(find "$STREAM/raw/segments" -mindepth 1 -maxdepth 1 -type d -name 'sess_*' 2>/dev/null | wc -l)
  ready_n=$(find "$STREAM/state/sessions" -name 'session.READY' 2>/dev/null | wc -l)
  log "uploaded=${raw_n}/${EPISODES} ready=${ready_n}/${EPISODES} elapsed=$((SECONDS - derive_wait_start))s"
  if [[ "$raw_n" -ge "$EPISODES" && "$ready_n" -ge "$EPISODES" ]]; then
    break
  fi
  sleep 10
done
raw_n=$(find "$STREAM/raw/segments" -mindepth 1 -maxdepth 1 -type d -name 'sess_*' 2>/dev/null | wc -l)
ready_n=$(find "$STREAM/state/sessions" -name 'session.READY' 2>/dev/null | wc -l)
  if [[ "$raw_n" -lt "$EPISODES" || "$ready_n" -lt "$EPISODES" ]]; then
  log "FAIL: derive 未全部 READY (raw=$raw_n ready=$ready_n)"
  exit 1
fi

derive_wait_sec=$((SECONDS - derive_wait_start))
log "derive READY ${ready_n}/${EPISODES} 用时 ${derive_wait_sec}s"

log "=== 4/4 ego-process (batch convert + P1 perf) ==="
# shellcheck source=ego-pipeline-performance.env.sh
source "${SCRIPT_DIR}/ego-pipeline-performance.env.sh"
# E2E: inline batch convert — predictable progress; avoid waiting on stale convert worker.
export EGO_USE_CONVERT_WORKER=0
export EGO_EXPORT_REQUIRE_QC=0
export EGO_EXPORT_REQUIRE_ANNOTATION=0
process_start=$SECONDS
cd "$DATALAB"
bash "${SCRIPT_DIR}/ego-process" "$STATION" 2>&1 | tee -a "$RUN_LOG"
rc=${PIPESTATUS[0]}
log "ego-process 用时 $((SECONDS - process_start))s"
if [[ "$rc" -ne 0 ]]; then
  log "FAIL: ego-process exit=$rc"
  exit "$rc"
fi

log "=== 验收 ==="
corpus_eps=$(python3 -c "
import json
from pathlib import Path
p=Path('${DATALAB}/data-storage/corpus/egodome/meta/info.json')
print(json.loads(p.read_text()).get('total_episodes',0) if p.is_file() else 0)
")
manifests=$(find "${DATALAB}/data-storage/ego-delivery/orders" -path '*/episodes/*/meta/export_manifest.json' 2>/dev/null | wc -l)
log "corpus_episodes=$corpus_eps manifests=$manifests uploaded=$raw_n total_elapsed=$((SECONDS))s"
log "Collection: http://10.10.10.34:8080/collection?station=${STATION}"
log "Egodome:    http://10.10.10.34:8080/data/egodome"
log "日志: $RUN_LOG"
if [[ "$corpus_eps" -ge "$EPISODES" && "$manifests" -ge "$EPISODES" ]]; then
  log "🎉 E2E 全流程通过"
else
  log "⚠️  部分指标未达标，请查日志"
  exit 1
fi
