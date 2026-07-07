#!/usr/bin/env bash
# Rebuild data-lab-label-studio:local with baked frontend + CN PyPI mirror defaults.
# Run from repository root:
#   bash data-lab-platform/deploy-label-studio-image.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml)
BAKED_DIR="data-lab-platform/build-context/web-dist"
MAIN_JS="${BAKED_DIR}/apps/labelstudio/main.js"

sync_baked_frontend() {
  mkdir -p "$BAKED_DIR"
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'data-lab-app-1'; then
    if docker exec data-lab-app-1 test -f /label-studio/web/dist/apps/labelstudio/main.js \
      && docker exec data-lab-app-1 grep -q 'embodied-annotate' /label-studio/web/dist/apps/labelstudio/main.js; then
      echo "==> Syncing verified web/dist from data-lab-app-1 -> ${BAKED_DIR}/"
      rm -rf "${BAKED_DIR:?}"/*
      docker cp data-lab-app-1:/label-studio/web/dist/. "$BAKED_DIR/"
      date -Iseconds > "${BAKED_DIR}/.datalab-frontend-baked"
      return 0
    fi
  fi
  if [ -f "$MAIN_JS" ] && grep -q 'embodied-annotate' "$MAIN_JS"; then
    echo "==> Using existing baked frontend at ${BAKED_DIR}/"
    return 0
  fi
  echo "WARN: No baked frontend with embodied-annotate marker; image build will compile from source." >&2
}

verify_image_frontend() {
  local image="${LABEL_STUDIO_IMAGE:-data-lab-label-studio:local}"
  echo "==> Verifying frontend in image ${image}"
  docker run --rm "$image" grep -q 'embodied-annotate' /label-studio/web/dist/apps/labelstudio/main.js
  echo "==> OK: embodied-annotate present in image web/dist"
}

sync_baked_frontend

export DOCKER_BUILDKIT=1
echo "==> Building app + nginx (PYPI mirror: ${PYPI_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/})"
"${COMPOSE[@]}" build app nginx

verify_image_frontend

echo "==> Recreating app + nginx containers"
"${COMPOSE[@]}" up -d --force-recreate app nginx

echo "==> Verify /data page (embed manifest + bundled datasets)"
bash "${ROOT}/data-lab-platform/verify-data-page.sh" || exit 1

echo "==> Done. Frontend is baked into ${LABEL_STUDIO_IMAGE:-data-lab-label-studio:local}."
