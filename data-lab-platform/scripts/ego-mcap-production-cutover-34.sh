#!/usr/bin/env bash
# Phase 2 — 34 platform MCAP production cutover:
#   1. Cold-archive legacy ego-001 (tar.zst) + ego-mcap-pilot stream data
#   2. Promote ego-mcap-track2 → ego-001
#   3. Stop POC overlays (:7863/:7864)
# Protected: data-storage/corpus/, ego-archive/, registry/ — never bulk-deleted here.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STREAM="${ROOT}/data-storage/stream"
ARCHIVE="${ROOT}/data-storage/ego-archive"
DATE="${CUTOVER_DATE:-$(date +%Y%m%d)}"
DRY_RUN="${DRY_RUN:-0}"

log() { echo "[cutover-34] $*"; }
run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY_RUN: $*"
  else
    log "RUN: $*"
    eval "$@"
  fi
}

archive_stream_station() {
  local name="$1"
  local src="${STREAM}/${name}"
  local dest="${ARCHIVE}/${name}-${DATE}.tar.gz"
  if [[ ! -d "$src" ]]; then
    log "skip missing stream station: ${name}"
    return 0
  fi
  log "archiving ${name} → ${dest}"
  run "mkdir -p '${ARCHIVE}'"
  run "tar -czf '${dest}' -C '${STREAM}' '${name}'"
  if [[ "$DRY_RUN" != "1" ]]; then
    tar -tzf "${dest}" >/dev/null
    run "rm -rf '${src}'"
    log "archived+removed hot tier: ${name}"
  fi
}

log "=== Phase 2 cutover start (date=${DATE}) ==="

COMPOSE="${COMPOSE:-docker-compose}"
if ! command -v docker-compose >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
fi

log "stopping POC overlays (mcap-pilot / mcap-track2)"
run "cd '${ROOT}' && ${COMPOSE} -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml -f data-lab-platform/docker-compose.v0.1.4-mcap-pilot.yml --profile mcap-pilot down 2>/dev/null || true"
run "cd '${ROOT}' && ${COMPOSE} -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml -f data-lab-platform/docker-compose.v0.1.4-mcap-track2.yml --profile mcap-track2 down 2>/dev/null || true"

if [[ -d "${STREAM}/ego-mcap-track2" ]]; then
  archive_stream_station "ego-001"
  archive_stream_station "ego-mcap-pilot"
  log "promoting ego-mcap-track2 → ego-001"
  if [[ -d "${STREAM}/ego-001" ]]; then
    echo "❌ ego-001 still exists after archive — abort" >&2
    exit 1
  fi
  run "mv '${STREAM}/ego-mcap-track2' '${STREAM}/ego-001'"
else
  log "ego-mcap-track2 not found; assuming ego-001 already promoted"
fi

PIPE="${ROOT}/data-storage/pipeline"
if [[ -d "${PIPE}/ego-mcap-track2" ]]; then
  log "migrating pipeline/ego-mcap-track2 sessions → pipeline/ego-001"
  run "mkdir -p '${PIPE}/ego-001'"
  for sess_dir in "${PIPE}/ego-mcap-track2"/sess_*; do
    [[ -d "$sess_dir" ]] || continue
    sid="$(basename "$sess_dir")"
    if [[ -d "${PIPE}/ego-001/${sid}" ]]; then
      log "skip existing pipeline session ${sid}"
    else
      run "mv '${sess_dir}' '${PIPE}/ego-001/${sid}'"
    fi
  done
fi

log "rebuilding L2 view for ego-001"
if [[ "$DRY_RUN" != "1" && -d "${STREAM}/ego-001" ]]; then
  DATALAB_ROOT="${ROOT}" STREAM_DATA_ROOT="${STREAM}" \
    node "${ROOT}/data-lab-platform/lerobot-studio/ego-derive-run.mjs" rebuild-view --station ego-001 --json >/dev/null
fi

log "=== Phase 2 cutover done ==="
log "Next: bash data-lab-platform/scripts/deploy-stream-ingest-v0.1.4-mcap.sh"
