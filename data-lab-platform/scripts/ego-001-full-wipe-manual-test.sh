#!/usr/bin/env bash
# Full wipe for manual capture→upload→derive test: 130 cache + 34 ego-001 + egodome pipeline.
# Does NOT preserve raw/segments — clean slate.
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
EDGE_HOST="${EDGE_HOST:-server@10.10.10.130}"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"
SAMPLES_SLUG="${SAMPLES_SLUG:-egodome}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STREAM="${ROOT}/data-storage/stream/${STATION}"

log() { echo "[full-wipe] $*"; }
die() { echo "[full-wipe] ERROR: $*" >&2; exit 1; }

log "=== stop derive-worker (manual derive mode) ==="
docker stop data-lab-derive-worker-1 >/dev/null 2>&1 || true

log "=== 130: wipe segment cache ==="
export EDGE_HOST EDGE_PASS STATION
python3 - <<'PY'
import os, paramiko, sys

host = os.environ["EDGE_HOST"].split("@")[-1]
user = os.environ["EDGE_HOST"].split("@")[0]
password = os.environ["EDGE_PASS"]
station = os.environ["STATION"]
seg = f"/home/server/cache/{station}/segments"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=password, timeout=20)

script = f"""
set -euo pipefail
systemctl --user stop ecs-oak-capture-stack.target 2>/dev/null || true
systemctl --user stop ecs-record-oak-stream.service 2>/dev/null || true
systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
rm -rf {seg}/sessions {seg}/registry.json {seg}/checkpoint.json {seg}/strict_emit_ts.json
mkdir -p {seg}
rm -rf /home/server/cache/{station}/logs/* 2>/dev/null || true
rm -rf /home/server/export/{station} 2>/dev/null || true
rm -rf /dev/shm/ego-capture-active 2>/dev/null || true
systemctl --user restart ecs-ego-web.service 2>/dev/null || true
echo "130 cleared: $(du -sh {seg} 2>/dev/null | cut -f1)"
"""
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{script}\nREMOTE", timeout=120)
print((o.read() + e.read()).decode().strip())
if o.channel.recv_exit_status() != 0:
    sys.exit(1)
c.close()
PY

log "=== 34: full wipe stream/${STATION} (raw + derived + manifest) ==="
if [[ -d "${STREAM}" ]]; then
  rm -rf "${STREAM}"
fi
mkdir -p "${STREAM}/raw/segments" "${STREAM}/state/sessions"
chmod -R u+rwX "${STREAM}" 2>/dev/null || true

log "=== 34: wipe pipeline/corpus/samples (${SAMPLES_SLUG}) ==="
rm -rf "${ROOT}/data-storage/pipeline/${STATION}" 2>/dev/null || true
rm -rf "${ROOT}/data-storage/corpus/${SAMPLES_SLUG}" "${ROOT}/data-storage/corpus/${SAMPLES_SLUG}.zip" 2>/dev/null || true
rm -rf "${ROOT}/data-storage/samples/${SAMPLES_SLUG}"* 2>/dev/null || true
rm -f "${ROOT}/data-storage/samples/${SAMPLES_SLUG}_hand_kp2d.json" \
  "${ROOT}/data-storage/samples/${SAMPLES_SLUG}_depth_preview.json" 2>/dev/null || true
rm -rf "${ROOT}/data-storage/samples/${SAMPLES_SLUG}_depth_preview_frames" 2>/dev/null || true
rm -rf "${ROOT}/data-storage/ego-archive/${STATION}" 2>/dev/null || true
mkdir -p "${ROOT}/data-storage/ego-archive/${STATION}"

PIPE_ROOT="${EGO_HAND_PIPELINE_ROOT:-$(dirname "$ROOT")/ego-hand-pipeline}"
if [[ -d "${PIPE_ROOT}/outputs" ]]; then
  rm -rf "${PIPE_ROOT}/outputs/${STATION}" "${PIPE_ROOT}/outputs/dataset/EgoDome" 2>/dev/null || true
  rm -f "${PIPE_ROOT}/outputs/dataset/${SAMPLES_SLUG}.zip" 2>/dev/null || true
fi

log "=== Docker: clear bundled ${SAMPLES_SLUG} ==="
if docker ps --format '{{.Names}}' | grep -q '^data-lab-lerobot-1$'; then
  docker exec data-lab-lerobot-1 sh -c "
    rm -rf /srv/bundled/${SAMPLES_SLUG} /srv/bundled/${SAMPLES_SLUG}.zip \
      /srv/bundled/covers/${SAMPLES_SLUG}.webp \
      /srv/bundled/overlays/${SAMPLES_SLUG}_* 2>/dev/null || true
  "
fi

log "=== verify empty ==="
raw_n="$(find "${STREAM}/raw/segments" -name '*.tar.zst' 2>/dev/null | wc -l | tr -d ' ')"
derived_n="$(find "${STREAM}/derived" -name 'unit.json' 2>/dev/null | wc -l | tr -d ' ')"
status="$(curl -sf --max-time 10 "http://127.0.0.1:8080/lerobot/api/collection/stations/${STATION}/derive-status" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); p=d['progress']; print(d['phase'],p['markers'],p['total'],p['parquetRows'])" 2>/dev/null || echo "unavailable")"
log "raw=${raw_n} units=${derived_n} derive-status=${status}"
log "done — manual flow: capture on 130 → upload_segments → ego-derive run --session <sess>"
