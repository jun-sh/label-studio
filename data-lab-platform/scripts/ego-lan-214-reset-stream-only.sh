#!/usr/bin/env bash
# P0: Wipe 34 stream layer only for ego-lan-214 re-import.
# Does NOT touch 214 export/ready/, pipeline outputs, or samples.
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STREAM_HOST="${ROOT}/data-storage/stream/${STATION}"
STREAM_DEV="${ROOT}/data-lab-platform/stream-data/${STATION}"

log() { echo "[reset-stream] $*"; }
die() { echo "[reset-stream] ERROR: $*" >&2; exit 1; }

confirm() {
  if [[ "${EGO_RESET_YES:-}" == "1" ]]; then
    return 0
  fi
  if [[ "${1:-}" == "--yes" ]]; then
    return 0
  fi
  echo "This will DELETE stream data on 34 only:"
  echo "  ${STREAM_HOST}"
  echo "  ${STREAM_DEV} (if present)"
  echo ""
  echo "SAFE: does NOT SSH to 214, does NOT delete:"
  echo "  /home/server/export/ego-lan-214/ready/ on 214"
  echo "  data-storage/samples/ or pipeline outputs"
  echo ""
  echo "Set EGO_RESET_YES=1 to skip this prompt."
  read -r -p "Continue? [y/N] " ans
  [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]]
}

[[ "${1:-}" != "--yes" ]] || shift
confirm "${1:-}" || die "aborted"

log "=== 34: wipe stream layer (${STATION}) ==="
if ! rm -rf "${STREAM_HOST}" 2>/dev/null; then
  docker run --rm -v "${ROOT}/data-storage/stream:/srv/stream" alpine \
    sh -c "rm -rf /srv/stream/${STATION} && mkdir -p /srv/stream/${STATION} && chown 1000:1000 /srv/stream/${STATION}"
else
  mkdir -p "${STREAM_HOST}"
  chown 1000:1000 "${STREAM_HOST}" 2>/dev/null || true
fi

rm -rf "${STREAM_DEV}" 2>/dev/null || true

log "=== Docker: restart stream-ingest + lerobot + nginx ==="
cd "${ROOT}"
docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml \
  restart stream-ingest lerobot nginx >/dev/null

sleep 2
frames="$(curl -sf "http://127.0.0.1:8080/lerobot/api/stream/${STATION}/status" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('totalFrames', '?'))" 2>/dev/null || echo "?")"
log "stream status totalFrames=${frames} (expect 0 or scaffold only)"
log "done — re-import 21 seg_*.tar.zst from 214 ready/ via collection page"
log "then run: bash data-lab-platform/scripts/ego-lan-214-reimport-verify.sh"
