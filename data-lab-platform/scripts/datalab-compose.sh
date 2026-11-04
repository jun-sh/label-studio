#!/usr/bin/env bash
# Canonical Data Lab docker-compose entrypoint.
#
# Uses production overlay stack (base + platform + storage + EGO_PRODUCTION_TAG).
# Run from anywhere; cwd is switched to the repo root automatically.
#
# Examples:
#   ./data-lab-platform/scripts/datalab-compose.sh ps
#   ./data-lab-platform/scripts/datalab-compose.sh up -d --no-build lerobot
#   ./data-lab-platform/scripts/datalab-compose.sh up -d embodied-annotate nginx
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# shellcheck source=ego-production-defaults.sh
source "$(dirname "$0")/ego-production-defaults.sh"

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
elif docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
else
  echo "ERROR: docker-compose or 'docker compose' not found" >&2
  exit 1
fi

COMPOSE+=(-f docker-compose.yml)
while IFS= read -r f; do
  [[ "$f" == "docker-compose.yml" ]] && continue
  COMPOSE+=(-f "$f")
done < <(ego_compose_files_for_mode "${EGO_DERIVE_MODE_DEFAULT:-}" "$ROOT")

exec "${COMPOSE[@]}" "$@"
