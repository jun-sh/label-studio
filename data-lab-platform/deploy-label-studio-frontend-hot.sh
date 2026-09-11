#!/usr/bin/env bash
# Hot-deploy Label Studio frontend only (no image rebuild).
#
# Use this for React/TS changes under web/ (e.g. DataViz, EmbodiedAnnotate embed).
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
STAMP="$ROOT/data-lab-platform/build-context/.frontend-hot-build"
NODE_IMAGE="${LABEL_STUDIO_NODE_IMAGE:-node:20-bookworm}"
# User-owned staging avoids root-owned dist.hot-staging permission failures.
STAGING="${FRONTEND_HOT_STAGING:-$(mktemp -d "${TMPDIR:-/tmp}/datalab-frontend-build.XXXXXX")}"

log() {
  echo "[$(date '+%H:%M:%S')] $*"
}

cleanup() {
  if [[ "${KEEP_FRONTEND_STAGING:-0}" != "1" && -d "$STAGING" ]]; then
    rm -rf "$STAGING"
  fi
}
trap cleanup EXIT

for c in data-lab-app-1 data-lab-nginx-1; do
  if ! docker ps --format '{{.Names}}' | grep -qx "$c"; then
    echo "ERROR: container $c is not running. Start the stack first." >&2
    exit 1
  fi
done

log "Building Label Studio frontend in ${NODE_IMAGE}"
log "Staging dir: ${STAGING}"

docker run --rm \
  -v "${ROOT}:/repo:ro" \
  -v "${STAGING}:/out" \
  -e NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=6144}" \
  -e BUILD_NO_MINIMIZATION="${BUILD_NO_MINIMIZATION:-}" \
  "$NODE_IMAGE" \
  bash -c 'set -euo pipefail
    step() { echo "[build $(date +%H:%M:%S)] $*"; }
    step "copy sources"
    rm -rf /tmp/ls-web && cp -a /repo/web /tmp/ls-web
    cp /repo/pyproject.toml /tmp/pyproject.toml
    cd /tmp/ls-web
    yarn config set registry https://registry.npmmirror.com
    step "yarn install"
    yarn install --ignore-engines
    step "nx production build"
    yarn nx run labelstudio:build:production --skip-nx-cache
    step "export dist"
    rm -rf /out/* && cp -a dist/. /out/
    test -f /out/apps/labelstudio/main.js
  '

MAIN_JS="${STAGING}/apps/labelstudio/main.js"
if [[ ! -f "$MAIN_JS" ]]; then
  echo "ERROR: build did not produce ${MAIN_JS}" >&2
  exit 1
fi

mkdir -p "$(dirname "$STAMP")"
date -Iseconds > "$STAMP"

if ! grep -q 'embodied-annotate' "$MAIN_JS"; then
  log "WARN: embodied-annotate marker not found in main.js"
fi

HOST_DIST="${WEB}/dist"
if [[ -d "$HOST_DIST" ]] && [[ -w "$HOST_DIST" ]]; then
  log "Sync host web/dist"
  rm -rf "$HOST_DIST"
  cp -a "$STAGING/." "$HOST_DIST/"
elif [[ -d "$HOST_DIST" ]]; then
  log "WARN: ${HOST_DIST} not writable; skipping host sync"
fi

STATIC_BUILD="${ROOT}/label_studio/core/static_build"
if [[ -d "$STATIC_BUILD" ]] && [[ -w "$STATIC_BUILD" ]]; then
  log "Sync label_studio/core/static_build (bind-mounted into app)"
  rm -rf "${STATIC_BUILD:?}/"*
  cp -a "$STAGING/." "$STATIC_BUILD/"
fi

log "Copy build into running containers"
docker cp "$STAGING/." data-lab-app-1:/label-studio/web/dist/
docker cp "$STAGING/." data-lab-nginx-1:/label-studio/web/dist/

log "Done. Hard-refresh http://10.10.10.34:8080 (Ctrl+Shift+R)."
log "Built at: $(cat "$STAMP")"
