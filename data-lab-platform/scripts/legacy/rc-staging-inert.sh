#!/usr/bin/env bash
# Verify segment_mp4 production path does not write staging JPEGs or invoke staging mux.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${RC_STATION:-ego-001}"
CID="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
STREAM="${ROOT}/data-storage/stream/${STATION}"

echo "=== RC-4 staging mux inert (segment_mp4) ==="

docker exec "${CID}" node -e "
import { isSegmentMp4PrimaryPath } from '/app/segment-mp4-ingest.mjs';
if (!isSegmentMp4PrimaryPath()) process.exit(2);
console.log('primary=segment_mp4');
"

STAGING="${STREAM}/_staging"
if [[ -d "${STAGING}" ]]; then
  jpg_count=$(find "${STAGING}" -name 'frame_*.jpg' 2>/dev/null | wc -l)
  if [[ "${jpg_count}" -gt 0 ]]; then
    echo "WARN: ${jpg_count} staging jpgs present (legacy residue); segment_mp4 should not add more"
  fi
fi

if docker logs "${CID}" 2>&1 | tail -300 | grep -qE 'mux_staging|staging_purge|mux_one_camera'; then
  echo "FAIL: staging mux activity in recent stream-ingest logs"
  exit 1
fi

echo "RC-4 ok: no staging mux activity in recent logs"
echo "=== RC-4 PASSED ==="
