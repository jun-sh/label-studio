#!/usr/bin/env bash
# Provision a new EGO collection station on Data Lab (34).
# Usage: ./provision-collection-station.sh <station_id> [host_ip]
set -euo pipefail

STATION_ID="${1:?station_id required, e.g. ego-001}"
HOST_IP="${2:-10.10.10.130}"
TOKEN="dl-upload-${STATION_ID}-v1"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STREAM_ROOT="${ROOT}/data-storage/stream/${STATION_ID}"
TEMPLATE="${ROOT}/data-storage/stream/ego-test-empty"

echo "==> Station: ${STATION_ID}"
echo "    Host:    ${HOST_IP}"
echo "    Token:   ${TOKEN}"

if [[ ! -d "${TEMPLATE}" ]]; then
  echo "ERROR: missing template ${TEMPLATE}" >&2
  exit 1
fi

if [[ ! -d "${STREAM_ROOT}" ]]; then
  echo "==> Create stream skeleton: ${STREAM_ROOT}"
  cp -a "${TEMPLATE}" "${STREAM_ROOT}"
  python3 - <<PY
import json
from pathlib import Path
root = Path(${STREAM_ROOT@Q})
for name in ("info.json", "info.viewer.json"):
    p = root / "meta" / name
    if not p.is_file():
        continue
    data = json.loads(p.read_text(encoding="utf-8"))
    cap = data.get("ego_capture")
    if isinstance(cap, dict):
        cap["stream_station"] = ${STATION_ID@Q}
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
else
  echo "==> Stream dir exists, skip skeleton"
fi

COMPOSE=(docker-compose -f "${ROOT}/docker-compose.yml" -f "${ROOT}/data-lab-platform/docker-compose.platform.yml")
echo "==> Recreate stream-ingest + lerobot (pick up config bind mounts)"
"${COMPOSE[@]}" up -d stream-ingest lerobot app

HOST_URL="${LABEL_STUDIO_HOST:-http://10.10.10.34:8080}"
echo ""
echo "==> Verify API"
curl -fsS "${HOST_URL}/lerobot/api/collection/stations" | python3 -c "
import json,sys
data=json.load(sys.stdin)
ids={s['id'] for s in data.get('stations',[])}
assert ${STATION_ID@Q} in ids, f'missing station in catalog: {ids}'
print('catalog ok:', ${STATION_ID@Q})
"

echo ""
echo "Done. Browser:"
echo "  ${HOST_URL}/collection?station=${STATION_ID}"
echo ""
echo "Edge agent env:"
echo "  STATION_ID=${STATION_ID}"
echo "  STATION_UPLOAD_TOKEN=${TOKEN}"
echo "  UPLOAD_URL=${HOST_URL}/lerobot/api/collection/stations/${STATION_ID}/upload"
