#!/usr/bin/env bash
set -euo pipefail
source /opt/datalab/env/stream-storage.env
echo "--- disk ---"
df -h "${STREAM_VOL_ROOT}" /var/lib/datalab 2>/dev/null || df -h /
echo "--- station usage ---"
du -sh "${STATION_ROOT}" "${STATION_ROOT}/archive" 2>/dev/null || true
du -sh "${COLD_ROOT}" 2>/dev/null || true
echo "--- ingest status (diskUsagePercent) ---"
timeout 15 docker exec data-lab-lerobot-1 wget -qO- --timeout=12 \
  "http://127.0.0.1:7860/lerobot/api/stream/${STATION_ID}/status" 2>/dev/null | python3 -m json.tool || \
  echo "status API unavailable (lerobot container)"
echo "--- via nginx :8080 ---"
timeout 8 curl -fsS --max-time 6 "http://127.0.0.1:8080/lerobot/api/stream/${STATION_ID}/status" 2>/dev/null | python3 -m json.tool || \
  echo "nginx route skipped or 502 (use docker exec above)"
echo "--- in-container usage ---"
docker exec data-lab-stream-ingest-1 du -sh "/srv/stream/${STATION_ID}" 2>/dev/null || true
echo "--- docker env quota ---"
docker exec data-lab-stream-ingest-1 printenv 2>/dev/null | grep -E '^STREAM_' || true
