#!/usr/bin/env bash
# Canonical Data Lab docker-compose entrypoint.
#
# Always uses the full overlay stack (base + platform + storage).
# Run from anywhere; cwd is switched to the repo root automatically.
#
# Examples:
#   ./data-lab-platform/scripts/datalab-compose.sh ps
#   ./data-lab-platform/scripts/datalab-compose.sh up -d embodied-annotate nginx
#   ./data-lab-platform/scripts/datalab-compose.sh up -d --force-recreate embodied-annotate
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
elif docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
else
  echo "ERROR: docker-compose or 'docker compose' not found" >&2
  exit 1
fi

COMPOSE+=(
  -f docker-compose.yml
  -f data-lab-platform/docker-compose.platform.yml
  -f data-lab-platform/docker-compose.storage.override.yml
)

exec "${COMPOSE[@]}" "$@"
