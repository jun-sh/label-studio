#!/usr/bin/env bash
# One-shot deploy for deploy-release v0.1.7 (MCAP stack + platform services + frontend).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

log() {
  echo "[deploy-v0.1.7 $(date '+%H:%M:%S')] $*"
}

log "1/4 MCAP stream-ingest + derive-worker + lerobot"
bash "${ROOT}/data-lab-platform/scripts/deploy-stream-ingest-v0.1.4-mcap.sh"

log "2/4 auxiliary services (parquet-sync, annotate, qc)"
docker-compose -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.v0.1.3-async.yml \
  -f data-lab-platform/docker-compose.v0.1.4-mcap.yml \
  up -d --force-recreate stream-parquet-sync embodied-annotate lerobot-qc derive-worker

docker tag data-lab-lerobot-studio:v0.1.4-mcap-rc data-lab-lerobot-studio:v0.1.7 2>/dev/null || true

log "3/4 Label Studio frontend hot deploy"
bash "${ROOT}/data-lab-platform/deploy-label-studio-frontend-hot.sh"

log "4/4 health checks"
curl -fsS --max-time 10 "${LABEL_STUDIO_HOST:-http://10.10.10.34:8080}/health/" >/dev/null
docker exec data-lab-stream-ingest-1 wget -qO- http://127.0.0.1:7862/healthz >/dev/null
docker exec data-lab-lerobot-1 wget -qO- http://127.0.0.1:7860/lerobot/ >/dev/null
bash "${ROOT}/data-lab-platform/verify-data-page.sh"

log "Done — v0.1.7 deployed."
