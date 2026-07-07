#!/usr/bin/env bash
# Reset false-positive derived → verified and restart derive queue (34 only). Raw tar.zst untouched.
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SEG_JSON="${ROOT}/data-storage/stream/${STATION}/state/segments.json"
COMPOSE="docker-compose -f ${ROOT}/docker-compose.yml -f ${ROOT}/data-lab-platform/docker-compose.platform.yml"

if [[ ! -f "${SEG_JSON}" ]]; then
  echo "missing ${SEG_JSON}" >&2
  exit 2
fi

python3 - "$SEG_JSON" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
store = json.loads(p.read_text())
n = 0
for s in store.get("segments", {}).values():
    if s.get("status") in ("derived", "failed", "derive_failed"):
        s["status"] = "verified"
        s.pop("derivedAt", None)
        s["ready"] = False
        s["inDlq"] = False
        s["errorMsg"] = None
        s["failedStage"] = None
        n += 1
store["revision"] = int(store.get("revision", 0)) + 1
p.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n")
print(f"reset {n} segments → verified (UPLOADED)")
PY

echo "restarting stream-ingest (resume derive queue)…"
cd "${ROOT}"
DERIVE_ASYNC_EGO_LAN_214=1 ${COMPOSE} up -d stream-ingest >/dev/null
${COMPOSE} restart stream-ingest
sleep 3
echo "done — derive queue will resume from raw/ (DERIVE_ASYNC must be enabled)"
