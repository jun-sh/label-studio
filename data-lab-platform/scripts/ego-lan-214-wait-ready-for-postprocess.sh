#!/usr/bin/env bash
# Wait until stream ingest has muxed mp4 + parquet (postprocess precheck prerequisites).
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STREAM="${ROOT}/data-storage/stream/${STATION}"
TIMEOUT_SEC="${WAIT_TIMEOUT_SEC:-1800}"
INTERVAL_SEC="${WAIT_INTERVAL_SEC:-5}"

log() { echo "[wait-ready] $*"; }
die() { echo "[wait-ready] ERROR: $*" >&2; exit 1; }

[[ -d "$STREAM" ]] || die "missing stream dir: $STREAM"

deadline=$((SECONDS + TIMEOUT_SEC))

check_once() {
  local ok=1
  [[ -f "${STREAM}/meta/info.json" ]] || ok=0
  [[ -f "${STREAM}/data/chunk-000/file-000.parquet" ]] || ok=0
  [[ -f "${STREAM}/meta/episodes/chunk-000/file-000.parquet" ]] || ok=0
  if ! find "${STREAM}/videos" -name '*.mp4' -print -quit 2>/dev/null | grep -q .; then
    ok=0
  fi
  return $((1 - ok))
}

log "waiting for parquet + mp4 under ${STREAM} (timeout ${TIMEOUT_SEC}s)"

while (( SECONDS < deadline )); do
  if check_once; then
    frames="$(python3 -c "
import json
from pathlib import Path
p = Path('${STREAM}/meta/info.json')
print(json.loads(p.read_text()).get('total_frames', '?'))
" 2>/dev/null || echo "?")"
    session="$(python3 -c "
import json
from pathlib import Path
p = Path('${STREAM}/live/session.json')
print(json.loads(p.read_text()).get('sessionId', '?'))
" 2>/dev/null || echo "?")"
    log "ready: total_frames=${frames} sessionId=${session}"
    log "next: cd ego-hand-pipeline && ./scripts/ego-postprocess.sh ${STATION} ${session}"
    exit 0
  fi
  sleep "${INTERVAL_SEC}"
done

die "timeout after ${TIMEOUT_SEC}s — check stream-ingest logs and collection import status"
