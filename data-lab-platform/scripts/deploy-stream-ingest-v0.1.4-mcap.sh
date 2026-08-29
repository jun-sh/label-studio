#!/usr/bin/env bash
# Deploy MCAP stack on 34: pilot overlay (:7863) + image build.
# Production stream-ingest on :7862 (v0.1.3) is NOT replaced.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PILOT_SH="${ROOT}/data-lab-platform/scripts/deploy-stream-ingest-v0.1.4-mcap-pilot.sh"

echo "[deploy-mcap] building + starting MCAP pilot overlay (ingest :7863, derive-worker-mcap-pilot)"
bash "${PILOT_SH}"

echo "[deploy-mcap] bootstrap pilot stream meta (camera intrinsics)"
bash "${ROOT}/data-lab-platform/scripts/ego-mcap-pilot-bootstrap-34.sh"

echo ""
echo "[deploy-mcap] post-deploy checks:"
echo "  docker ps --filter name=mcap-pilot --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'"
echo "  curl -s http://127.0.0.1:7863/lerobot/api/collection/stations/ego-mcap-pilot/derive-status | jq ."
echo "  production ingest unchanged: curl -s http://127.0.0.1:7862/health"
echo ""
echo "Done. Pilot :7863 only; ego-001 production path on :7862 unchanged."
