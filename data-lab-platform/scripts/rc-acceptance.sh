#!/usr/bin/env bash
# JPEG/staging-mux acceptance: healthz + ingest topology + derive-async off.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CID="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
STATION="${RC_STATION:-ego-001}"
STREAM_ROOT="${STREAM_ROOT:-${ROOT}/data-storage/stream}"

echo "=== RC-3 healthz (60s after restart) ==="
"${ROOT}/data-lab-platform/scripts/rc-healthz-smoke.sh"

echo "=== RC-2 JPEG ingest baseline ==="
docker exec "${CID}" node -e "
import fs from 'node:fs';
const topo = JSON.parse(fs.readFileSync('/app/config/station-topology.json','utf8'));
const keys = topo['ego-standard'].video_keys;
const want = [
  'observation.images.camera_front_left',
  'observation.images.camera_front_right',
  'observation.images.camera_rear_left',
  'observation.images.camera_rear_right',
];
if (JSON.stringify(keys) !== JSON.stringify(want)) {
  console.error('FAIL ego-standard keys', keys);
  process.exit(1);
}
await import('/app/stream-ingest.mjs');
await import('/app/derive-pipeline.mjs');
console.log('RC-2 ok ego-standard + stream-ingest modules');
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

echo "=== RC-2d ready-gate + sensor_raw modules in image ==="
docker exec "${CID}" node -e "
import fs from 'node:fs';
for (const f of ['ready-gate.mjs','lifecycle-gc.mjs','pipeline.mjs']) {
  if (!fs.existsSync('/app/derive/' + f)) { console.error('missing', f); process.exit(1); }
}
if (fs.existsSync('/app/scripts/ingest-imu-high-freq.py')) {
  console.error('ingest-imu-high-freq.py must be removed'); process.exit(1);
}
console.log('RC-2d ok derive phase5 modules');
"

IMU_REL="${STREAM_ROOT}/${STATION}/sensor_raw/imu/chunk-000/file-000.parquet"
if [[ -f "${IMU_REL}" ]]; then
  echo "RC sensor_raw imu parquet present"
else
  echo "RC sensor_raw imu parquet absent (ok if no derive yet)"
fi

echo "=== RC-2c no segment_mp4 fallback in recent logs ==="
if docker logs "${CID}" 2>&1 | tail -200 | grep -qE 'segment_mp4|segment-mp4-ingest'; then
  echo "FAIL: segment_mp4 references in recent logs"
  exit 1
fi
echo "RC-2c ok (no segment_mp4 activity)"

MP4_COUNT=$(find "${STREAM_ROOT}/${STATION}/videos" -name '*.mp4' 2>/dev/null | wc -l)
echo "RC dataset mp4_count=${MP4_COUNT}"

echo "=== derive-status latency (5 requests) ==="
for _ in 1 2 3 4 5; do
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

echo "=== RC acceptance (JPEG path) PASSED ==="
