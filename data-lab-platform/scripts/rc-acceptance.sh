#!/usr/bin/env bash
# v0.0.9+ acceptance: segment_mp4 primary path + healthz + derive-status.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CID="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
STATION="${RC_STATION:-ego-001}"
STREAM_ROOT="${STREAM_ROOT:-${ROOT}/data-storage/stream}"

echo "=== RC-3 healthz (60s after restart) ==="
"${ROOT}/data-lab-platform/scripts/rc-healthz-smoke.sh"

echo "=== RC-2 primary path (segment_mp4, no frame push) ==="
docker exec "${CID}" node -e "
import {
  isSegmentMp4PrimaryPath,
  isStreamFramePushEnabled,
} from '/app/segment-mp4-ingest.mjs';
if (!isSegmentMp4PrimaryPath()) {
  console.error('FAIL: STREAM_PRIMARY_PATH is not segment_mp4');
  process.exit(1);
}
if (isStreamFramePushEnabled()) {
  console.error('FAIL: STREAM_FRAME_PUSH must be 0');
  process.exit(1);
}
console.log('RC-2 ok primary=segment_mp4 frame_push=0');
"

echo "=== RC-2b derive async disabled for ${STATION} ==="
ASYNC_GLOBAL="$(docker exec "${CID}" printenv DERIVE_ASYNC 2>/dev/null || echo 0)"
STATION_KEY="$(echo "${STATION}" | tr '[:lower:]' '[:upper:]' | tr '-' '_')"
ASYNC_STATION="$(docker exec "${CID}" printenv "DERIVE_ASYNC_${STATION_KEY}" 2>/dev/null || echo "")"
if [[ "${ASYNC_GLOBAL}" != "0" ]]; then
  echo "FAIL: DERIVE_ASYNC=${ASYNC_GLOBAL} (expected 0)"
  exit 1
fi
if [[ -n "${ASYNC_STATION}" && "${ASYNC_STATION}" != "0" ]]; then
  echo "FAIL: DERIVE_ASYNC_${STATION_KEY}=${ASYNC_STATION} (expected 0 or unset)"
  exit 1
fi
echo "RC-2b ok DERIVE_ASYNC=0 station_override=${ASYNC_STATION:-unset}"

echo "=== RC-2c no staging fallback in recent logs ==="
if docker logs "${CID}" 2>&1 | tail -200 | grep -q 'segment_mp4_fallback_staging'; then
  echo "FAIL: segment_mp4_fallback_staging found in recent logs"
  exit 1
fi
echo "RC-2c ok (no fallback_staging)"

MP4_COUNT=$(find "${STREAM_ROOT}/${STATION}/videos" -name '*.mp4' 2>/dev/null | wc -l)
echo "RC dataset mp4_count=${MP4_COUNT}"

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
