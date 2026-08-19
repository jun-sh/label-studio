#!/usr/bin/env bash
# Full reset for ego-001 re-run: 130 edge cache + 34 derived wipe (preserve raw/segments).
# Run on Data Lab host (10.10.10.34).
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
EDGE_HOST="${EDGE_HOST:-server@10.10.10.130}"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"
SAMPLES_SLUG="${SAMPLES_SLUG:-egodome}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

log() { echo "[reset-ego-001] $*"; }
die() { echo "[reset-ego-001] ERROR: $*" >&2; exit 1; }

confirm() {
  if [[ "${EGO_RESET_YES:-}" == "1" ]]; then
    return 0
  fi
  if [[ "${1:-}" == "--yes" ]]; then
    return 0
  fi
  echo "DELETE ego-001 rebuildable caches on 130 + 34 derived data."
  echo "PRESERVE 34 raw/segments/*.tar.zst. Set EGO_RESET_YES=1 to skip prompt."
  read -r -p "Continue? [y/N] " ans
  [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]]
}

[[ "${1:-}" != "--yes" ]] || shift
confirm "${1:-}" || die "aborted"

log "=== 130: stop capture + wipe rebuildable segment cache ==="
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
systemctl --user stop ecs-oak-standby-stack.target 2>/dev/null || true
rm -rf {seg}/sessions
rm -f {seg}/registry.json {seg}/checkpoint.json {seg}/strict_emit_ts.json
mkdir -p {seg}
rm -rf /home/server/cache/{station}/logs/* 2>/dev/null || true
rm -rf /home/server/export/{station} 2>/dev/null || true
rm -rf /dev/shm/ego-capture-active 2>/dev/null || true
systemctl --user enable ecs-station-heartbeat.service 2>/dev/null || true
systemctl --user start ecs-station-heartbeat.service 2>/dev/null || true
systemctl --user restart ecs-ego-web.service 2>/dev/null || true
echo "130 segments cache cleared: $(du -sh {seg} 2>/dev/null | cut -f1)"
"""
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{script}\nREMOTE", timeout=120)
out = (o.read() + e.read()).decode()
code = o.channel.recv_exit_status()
print(out.strip())
if code != 0:
    sys.exit(code)
c.close()
PY

log "=== 34: derived wipe via ego-reset-34-only (preserve raw/segments) ==="
STATION_ID="${STATION}" EGO_RESET_YES=1 bash "${SCRIPT_DIR}/ego-reset-34-only.sh" --yes

log "=== Docker: clear lerobot bundled egodome ==="
if docker ps --format '{{.Names}}' | grep -q '^data-lab-lerobot-1$'; then
  docker exec data-lab-lerobot-1 sh -c "
    rm -rf /srv/bundled/${SAMPLES_SLUG} /srv/bundled/${SAMPLES_SLUG}.zip \
      /srv/bundled/covers/${SAMPLES_SLUG}.webp \
      /srv/bundled/overlays/${SAMPLES_SLUG}_hand_kp2d.json \
      /srv/bundled/overlays/${SAMPLES_SLUG}_depth_preview.json \
      /srv/bundled/overlays/${SAMPLES_SLUG}_depth_preview_frames 2>/dev/null || true
  "
fi

sleep 2
STREAM_HOST="${ROOT}/data-storage/stream/${STATION}"
raw_count="$(find "${STREAM_HOST}/raw/segments" -name '*.tar.zst' 2>/dev/null | wc -l | tr -d ' ')"
frames="$(curl -sf "http://127.0.0.1:8080/lerobot/api/stream/${STATION}/status" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('totalFrames', '?'))" 2>/dev/null || echo "?")"
log "34 raw archives preserved: ${raw_count}; collection totalFrames=${frames}"
log "done — capture on 130, upload_segments --force or ego-derive run from raw"
