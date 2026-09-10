#!/usr/bin/env bash
# Revert P0 commercial optimizations — file-level restore from before/ snapshots.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
BEFORE="${ROOT}/data-lab-platform/rollback/p0-commercial-v0.1.3/before"

restore() {
  local name="$1"
  local dest="$2"
  if [[ -f "${BEFORE}/${name}" ]]; then
    cp "${BEFORE}/${name}" "${ROOT}/${dest}"
    echo "restored ${dest}"
  fi
}

restore receive-tar.mjs data-lab-platform/lerobot-studio/ingest/receive-tar.mjs
restore receive-mcap.mjs data-lab-platform/lerobot-studio/ingest/receive-mcap.mjs
restore io.mjs data-lab-platform/lerobot-studio/ingest/io.mjs
restore ingest-server.mjs data-lab-platform/lerobot-studio/ingest-server.mjs
restore index.mjs data-lab-platform/lerobot-studio/ingest/index.mjs
restore ego-process data-lab-platform/scripts/ego-process
restore ego-pipeline-stations.yaml data-lab-platform/config/ego-pipeline-stations.yaml
restore collection-stations.json data-lab-platform/lerobot-studio/config/collection-stations.json

echo "P0 added modules not removed — delete manually if full rollback needed:"
echo "  protocol-guard.mjs delivery-status.mjs ego-export-delivery.py"
