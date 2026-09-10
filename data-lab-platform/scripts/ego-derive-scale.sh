#!/usr/bin/env bash
# Q1-4: scale derive-worker replicas with shared CPU budget split.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COUNT="${1:-3}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ego-production-defaults.sh
source "${SCRIPT_DIR}/ego-production-defaults.sh"

OVERLAY_MULTI="${EGO_COMPOSE_OVERLAY_MULTI:-data-lab-platform/docker-compose.v0.1.3-multi-worker.yml}"

COMPOSE=(docker compose)
if ! docker compose version &>/dev/null; then
  COMPOSE=(docker-compose)
fi

if ! [[ "$COUNT" =~ ^[1-9][0-9]*$ ]]; then
  echo "Usage: $(basename "$0") <worker-count>" >&2
  echo "  Example: $(basename "$0") 3" >&2
  exit 1
fi

export DERIVE_WORKER_COUNT="$COUNT"

echo "==> scaling derive-worker to ${COUNT} replicas (DERIVE_WORKER_COUNT=${COUNT})"

(cd "${ROOT}" && DERIVE_WORKER_COUNT="${COUNT}" "${COMPOSE[@]}" \
  -f docker-compose.yml \
  -f data-lab-platform/docker-compose.platform.yml \
  -f data-lab-platform/docker-compose.storage.override.yml \
  -f "${EGO_COMPOSE_OVERLAY}" \
  -f "${EGO_COMPOSE_OVERLAY_ASYNC}" \
  -f "${OVERLAY_MULTI}" \
  up -d --force-recreate --scale "derive-worker=${COUNT}" stream-ingest derive-worker lerobot)

nginx_cid="${NGINX_CONTAINER:-data-lab-nginx-1}"
if docker ps --format '{{.Names}}' | grep -q "^${nginx_cid}$"; then
  docker restart "${nginx_cid}" >/dev/null 2>&1 || true
  echo "nginx restarted (${nginx_cid})"
fi

sleep 5
echo
echo "=== derive-worker containers ==="
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep derive-worker || true
echo
echo "=== worker env sample ==="
for c in $(docker ps --format '{{.Names}}' | grep derive-worker | head -3); do
  echo "# ${c}"
  docker exec "$c" printenv DERIVE_WORKER_ID DERIVE_WORKER_COUNT DERIVE_CPU_BUDGET 2>/dev/null | sed 's/^/  /' || true
done
echo
echo "Done. CPU budget per worker ≈ DERIVE_CPU_BUDGET / ${COUNT}"
