#!/usr/bin/env bash
# Full reset for ego-001 re-run: 130 edge segments + 34 stream/collection/egodome.
# Run on Data Lab host (10.10.10.34).
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
EDGE_HOST="${EDGE_HOST:-server@10.10.10.130}"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"
SAMPLES_SLUG="${SAMPLES_SLUG:-egodome}"
AGGREGATE_NAME="${AGGREGATE_NAME:-EgoDome}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PIPE_ROOT="${EGO_HAND_PIPELINE_ROOT:-$(dirname "$ROOT")/ego-hand-pipeline}"
STREAM_HOST="${ROOT}/data-storage/stream/${STATION}"
ARCHIVE_HOST="${ROOT}/data-storage/ego-archive/${STATION}"
STREAM_DEV="${ROOT}/data-lab-platform/stream-data/${STATION}"

log() { echo "[reset-ego-001] $*"; }
die() { echo "[reset-ego-001] ERROR: $*" >&2; exit 1; }

confirm() {
  if [[ "${EGO_RESET_YES:-}" == "1" ]]; then
    return 0
  fi
  if [[ "${1:-}" == "--yes" ]]; then
    return 0
  fi
  echo "DELETE all ego-001 data on 130 + 34 (stream, pipeline, corpus, samples /data/egodome)."
  echo "Set EGO_RESET_YES=1 to skip prompt."
  read -r -p "Continue? [y/N] " ans
  [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]]
}

[[ "${1:-}" != "--yes" ]] || shift
confirm "${1:-}" || die "aborted"

log "=== 130: stop capture + wipe segments ==="
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
echo "130 segments: $(du -sh {seg} 2>/dev/null | cut -f1)"
"""
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{script}\nREMOTE", timeout=120)
out = (o.read() + e.read()).decode()
code = o.channel.recv_exit_status()
print(out.strip())
if code != 0:
    sys.exit(code)
c.close()
PY

log "=== 34: wipe stream (collection) ==="
if ! rm -rf "${STREAM_HOST}" 2>/dev/null; then
  docker run --rm -v "${ROOT}/data-storage/stream:/srv/stream" alpine \
    sh -c "rm -rf /srv/stream/${STATION} && mkdir -p /srv/stream/${STATION} && chown 1000:1000 /srv/stream/${STATION}"
else
  mkdir -p "${STREAM_HOST}"
fi
rm -rf "${STREAM_DEV}" 2>/dev/null || true
rm -rf "${ARCHIVE_HOST}"
mkdir -p "${ARCHIVE_HOST}"

log "=== 34: wipe pipeline ==="
rm -rf "${ROOT}/data-storage/pipeline/${STATION}"

log "=== 34: wipe corpus + samples (/data/egodome) ==="
rm -rf "${ROOT}/data-storage/corpus/${SAMPLES_SLUG}"
rm -f "${ROOT}/data-storage/corpus/${SAMPLES_SLUG}.zip"
rm -rf "${ROOT}/data-storage/samples/${SAMPLES_SLUG}"*
rm -f "${ROOT}/data-storage/samples/${SAMPLES_SLUG}.zip"*
rm -f "${ROOT}/data-storage/samples/${SAMPLES_SLUG}_hand_kp2d.json"
rm -f "${ROOT}/data-storage/samples/${SAMPLES_SLUG}_depth_preview.json"
rm -rf "${ROOT}/data-storage/samples/${SAMPLES_SLUG}_depth_preview_frames"

if [[ -d "${PIPE_ROOT}/outputs" ]]; then
  rm -rf "${PIPE_ROOT}/outputs/${STATION}"
  rm -rf "${PIPE_ROOT}/outputs/dataset/${AGGREGATE_NAME}"
  rm -f "${PIPE_ROOT}/outputs/dataset/${SAMPLES_SLUG}.zip"
fi

log "=== Docker: clear lerobot bundled egodome + restart ==="
if docker ps --format '{{.Names}}' | grep -q '^data-lab-lerobot-1$'; then
  docker exec data-lab-lerobot-1 sh -c "
    rm -rf /srv/bundled/${SAMPLES_SLUG} /srv/bundled/${SAMPLES_SLUG}.zip \
      /srv/bundled/covers/${SAMPLES_SLUG}.webp \
      /srv/bundled/overlays/${SAMPLES_SLUG}_hand_kp2d.json \
      /srv/bundled/overlays/${SAMPLES_SLUG}_depth_preview.json \
      /srv/bundled/overlays/${SAMPLES_SLUG}_depth_preview_frames 2>/dev/null || true
  "
fi

COMPOSE=(docker compose)
if ! docker compose version &>/dev/null; then
  COMPOSE=(docker-compose)
fi
cd "${ROOT}"
"${COMPOSE[@]}" -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f data-lab-platform/docker-compose.v0.0.11.2.yml \
  restart stream-ingest derive-worker lerobot nginx >/dev/null 2>&1 || \
"${COMPOSE[@]}" -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml \
  restart stream-ingest derive-worker lerobot nginx >/dev/null 2>&1 || true

sleep 3
frames="$(curl -sf "http://127.0.0.1:8080/lerobot/api/stream/${STATION}/status" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('totalFrames', '?'))" 2>/dev/null || echo "?")"
log "collection totalFrames=${frames} (expect 0)"
log "done — capture on 130, then upload_segments"
