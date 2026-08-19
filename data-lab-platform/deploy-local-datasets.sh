#!/usr/bin/env bash
# One-shot: copy configs, ingest 4 datasets into lerobot volume, restart service.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

SAMPLES_DIR="${ROOT}/data-storage/samples"

compose() {
  docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml "$@"
}

if [ ! -d "${SAMPLES_DIR}" ]; then
  echo "ERROR: missing ${SAMPLES_DIR} — create it and add the four sample archives." >&2
  exit 1
fi

echo "==> Recreate lerobot with samples mount: ${SAMPLES_DIR}"
compose up -d lerobot

echo "==> Sync studio configs into container (bind-mounted studio files skip docker cp)"
docker_cp_if_needed() {
  local src="$1"
  local dest="$2"
  if docker exec data-lab-lerobot-1 test -e "$dest" 2>/dev/null; then
    local host_path
    host_path="$(docker inspect data-lab-lerobot-1 --format '{{range .Mounts}}{{if eq .Destination "'"${dest}"'"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"
    if [[ -n "$host_path" && "$host_path" == "$(readlink -f "$src" 2>/dev/null || realpath "$src")" ]]; then
      echo "    skip bind mount: $dest"
      return 0
    fi
  fi
  docker cp "$src" "data-lab-lerobot-1:$dest"
}
docker_cp_if_needed data-lab-platform/lerobot-studio/config/sample-datasets.manifest.json /app/config/sample-datasets.manifest.json
docker_cp_if_needed data-lab-platform/lerobot-studio/config/datasets.json /app/config/datasets.json
docker_cp_if_needed data-lab-platform/lerobot-studio/config/collection-stations.json /app/config/collection-stations.json
docker_cp_if_needed data-lab-platform/lerobot-studio/ingest-server.mjs /app/ingest-server.mjs 2>/dev/null || true
docker_cp_if_needed data-lab-platform/lerobot-studio/ingest-bundled-datasets.sh /app/ingest-bundled-datasets.sh
docker exec data-lab-lerobot-1 chmod +x /app/ingest-bundled-datasets.sh 2>/dev/null || true
docker exec data-lab-lerobot-1 sh /app/patches/apply-branding.sh /srv/lerobot

echo "==> Ingest datasets (may take a few minutes for DualPiper tar)..."
docker exec data-lab-lerobot-1 sh /app/ingest-bundled-datasets.sh

echo "==> Restart lerobot + nginx"
compose restart lerobot nginx

echo "==> Wait for lerobot embed route"
HOST="${LABEL_STUDIO_HOST:-http://10.10.10.34:8080}"
for _ in $(seq 1 24); do
  if curl -fsS --max-time 5 "${HOST}/lerobot/?datalab_embed=1" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

echo "==> Verify /data page (embed manifest + bundled datasets)"
bash "${ROOT}/data-lab-platform/verify-data-page.sh" || exit 1

echo "==> Live stream parquet helper (optional, during capture):"
echo "    bash data-lab-platform/scripts/sync-stream-station.sh ego-001"

echo "==> Done. Open http://10.10.10.34:8080/collection"
