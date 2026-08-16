#!/usr/bin/env bash
# v0.0.7-rc acceptance: RC-2 (mux skip) + RC-3 (healthz). RC-1/RC-4 need segment upload (see rc-e2e-upload.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CID="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
STATION="${RC_STATION:-ego-001}"
STREAM_ROOT="${STREAM_ROOT:-${ROOT}/data-storage/stream}"

echo "=== RC-3 healthz (60s after restart) ==="
"${ROOT}/data-lab-platform/scripts/rc-healthz-smoke.sh"

echo "=== RC-2 empty staging must not wipe MP4 ==="
MP4_BEFORE=$(find "${STREAM_ROOT}/${STATION}/videos" -name '*.mp4' 2>/dev/null | wc -l)
docker exec "${CID}" node -e "
import { resumePendingStreamMuxForAllStations } from '/app/stream-ingest.mjs';
resumePendingStreamMuxForAllStations();
" >/dev/null
sleep 8
if ! docker logs "${CID}" 2>&1 | tail -30 | grep -q "mux_skip.*no_staging"; then
  echo "WARN: mux_skip not in recent logs (staging may be non-empty)"
fi
MP4_AFTER=$(find "${STREAM_ROOT}/${STATION}/videos" -name '*.mp4' 2>/dev/null | wc -l)
if [[ "${MP4_BEFORE}" -gt 0 && "${MP4_AFTER}" -lt "${MP4_BEFORE}" ]]; then
  echo "FAIL: MP4 count dropped ${MP4_BEFORE} -> ${MP4_AFTER}"
  exit 1
fi
echo "RC-2 ok (mp4 before=${MP4_BEFORE} after=${MP4_AFTER})"

echo "=== derive-status latency (5 requests) ==="
for i in 1 2 3 4 5; do
  docker exec "${CID}" node -e "
import http from 'node:http';
const t=Date.now();
http.get('http://127.0.0.1:7862/lerobot/api/collection/stations/${STATION}/derive-status', r => {
  r.resume(); r.on('end', () => {
    const ms=Date.now()-t;
    if (r.statusCode!==200||ms>3000) process.exit(1);
    console.log('derive-status', ms+'ms');
  });
}).on('error', () => process.exit(1));
" || { echo "FAIL derive-status slow or error"; exit 1; }
done

echo "=== RC acceptance (local) PASSED ==="
