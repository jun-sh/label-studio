#!/usr/bin/env bash
# A1: DERIVE_ASYNC=0 regression smoke — sync ingest path unchanged, rollback safe.
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE=(docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml)
CONTAINER="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
API_BASE="${API_BASE:-http://127.0.0.1:8080}"

pass=0
fail=0

ok() { echo "[PASS] $*"; pass=$((pass + 1)); }
bad() { echo "[FAIL] $*"; fail=$((fail + 1)); }

echo "=== ego-lan-214 sync regression smoke (DERIVE_ASYNC=0) ==="
echo "station: ${STATION}"
echo ""

# 1. Ensure stream-ingest running with default async OFF
if ! docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
  bad "container ${CONTAINER} not running"
else
  ok "stream-ingest container running"
fi

# 2. Env defaults inside container
env_check="$(docker exec "${CONTAINER}" printenv DERIVE_ASYNC 2>/dev/null || true)"
env_station="$(docker exec "${CONTAINER}" printenv "DERIVE_ASYNC_EGO_LAN_214" 2>/dev/null || true)"
if [[ -z "${env_check}" || "${env_check}" == "0" ]]; then
  ok "DERIVE_ASYNC=${env_check:-<unset>} (treated as 0)"
else
  bad "DERIVE_ASYNC=${env_check} (expected 0 or unset for sync smoke)"
fi
if [[ -z "${env_station}" || "${env_station}" == "0" ]]; then
  ok "DERIVE_ASYNC_EGO_LAN_214=${env_station:-<unset>} (async off for station)"
else
  bad "DERIVE_ASYNC_EGO_LAN_214=${env_station} (expected 0 or unset for sync smoke)"
fi

# 3. Module gate: isDeriveAsyncEnabled must be false
gate_out="$(docker exec "${CONTAINER}" node --input-type=module -e "
import { isDeriveAsyncEnabled } from '/app/derive-async.mjs';
const s = '${STATION}';
console.log(JSON.stringify({
  station: isDeriveAsyncEnabled(s),
  other: isDeriveAsyncEnabled('other-station'),
}));
" 2>&1)" || gate_out="ERROR:${gate_out}"
if echo "${gate_out}" | python3 -c "
import json,sys
raw=sys.stdin.read().strip()
if raw.startswith('ERROR:'):
    sys.exit(1)
d=json.loads(raw)
sys.exit(0 if d.get('station') is False and d.get('other') is False else 1)
" 2>/dev/null; then
  ok "isDeriveAsyncEnabled(${STATION})=false"
else
  bad "isDeriveAsyncEnabled gate failed: ${gate_out}"
fi

# 4. Health + stream status API (sync path still serves viewer)
if curl -sf "${API_BASE}/lerobot/api/stream/${STATION}/status" >/dev/null 2>&1; then
  ok "stream status API reachable"
else
  bad "stream status API unreachable at ${API_BASE}"
fi

if docker exec "${CONTAINER}" wget -qO- "http://127.0.0.1:7862/healthz" >/dev/null 2>&1; then
  ok "ingest /healthz OK"
else
  bad "ingest /healthz failed"
fi

# 5. derive-async modules mounted
if docker exec "${CONTAINER}" test -f /app/derive-async.mjs; then
  ok "derive-async.mjs mounted (inactive under DERIVE_ASYNC=0)"
else
  bad "derive-async.mjs missing in container"
fi

echo ""
echo "=== Summary: ${pass} passed, ${fail} failed ==="
if [[ "$fail" -gt 0 ]]; then
  exit 1
fi
echo "Sync regression smoke OK — safe to proceed with W2 gray (set DERIVE_ASYNC_EGO_LAN_214=1)."
