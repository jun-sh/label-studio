#!/usr/bin/env bash
# Hot-deploy Label Studio frontend only (no image rebuild).
#
# Use this for React/TS changes under web/ (e.g. EmbodiedAnnotate embed).
# Avoids fragile full `deploy-label-studio-image.sh` when yarn lockfile drifts.
#
# From repo root:
#   bash data-lab-platform/deploy-label-studio-frontend-hot.sh
#
# Requires: docker, running data-lab-app-1 + data-lab-nginx-1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WEB="$ROOT/web"
OUT="$ROOT/web/dist"
STAMP="$ROOT/data-lab-platform/build-context/.frontend-hot-build"
NODE_IMAGE="${LABEL_STUDIO_NODE_IMAGE:-node:20-bookworm}"

for c in data-lab-app-1 data-lab-nginx-1; do
  if ! docker ps --format '{{.Names}}' | grep -qx "$c"; then
    echo "ERROR: container $c is not running. Start the stack first." >&2
    exit 1
  fi
done

echo "==> Building Label Studio frontend in ${NODE_IMAGE} (isolated /tmp build)"
rm -rf "${OUT}.hot-staging"
mkdir -p "${OUT}.hot-staging"

docker run --rm \
  -v "${ROOT}:/repo:ro" \
  -v "${OUT}.hot-staging:/out" \
  -e NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=6144}" \
  "$NODE_IMAGE" \
  bash -c 'set -euo pipefail
    rm -rf /tmp/ls-web && cp -a /repo/web /tmp/ls-web
    cp /repo/pyproject.toml /tmp/pyproject.toml
    cd /tmp/ls-web
    yarn config set registry https://registry.npmmirror.com
    yarn install --ignore-engines
    yarn nx run labelstudio:build:production --skip-nx-cache
    rm -rf /out/* && cp -a dist/. /out/
    test -f /out/apps/labelstudio/main.js
  '

echo "==> Publishing build to host (dist.hot-staging)"
STAGING="${OUT}.hot-staging"
if [ -d "$OUT" ] && [ ! -w "$OUT" ]; then
  echo "WARN: ${OUT} not writable (often root-owned); keeping ${STAGING} as source of truth" >&2
elif [ -d "$OUT" ]; then
  rm -rf "$OUT"
  mv "$STAGING" "$OUT"
  STAGING="$OUT"
else
  mv "$STAGING" "$OUT"
  STAGING="$OUT"
fi
mkdir -p "$(dirname "$STAMP")"
date -Iseconds > "$STAMP"

if ! grep -q 'embodied-annotate' "$STAGING/apps/labelstudio/main.js"; then
  echo "WARN: embodied-annotate marker not found in main.js" >&2
fi

echo "==> Copying ${STAGING}/ into running containers"
docker cp "$STAGING/." data-lab-app-1:/label-studio/web/dist/
docker cp "$STAGING/." data-lab-nginx-1:/label-studio/web/dist/

echo "==> Done. Hard-refresh http://10.10.10.34:8080 (Ctrl+Shift+R)."
echo "    Built at: $(cat "$STAMP")"
