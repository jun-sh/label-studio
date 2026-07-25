#!/usr/bin/env bash
# embodied-annotate service operations (wraps datalab-compose.sh).
#
# Usage:
#   ./data-lab-platform/embodied-annotate/ops.sh up          # start + nginx
#   ./data-lab-platform/embodied-annotate/ops.sh recreate    # force-recreate (schema volumes, etc.)
#   ./data-lab-platform/embodied-annotate/ops.sh build
#   ./data-lab-platform/embodied-annotate/ops.sh logs
#   ./data-lab-platform/embodied-annotate/ops.sh health
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="${SCRIPT_DIR}/../scripts/datalab-compose.sh"
SERVICE=embodied-annotate
CONTAINER=data-lab-embodied-annotate-1
HOST="${LABEL_STUDIO_HOST:-http://127.0.0.1:8080}"

usage() {
  cat <<'EOF'
embodied-annotate ops

  up        Start embodied-annotate (+ nginx proxy)
  recreate  Force-recreate container (apply compose volume/env changes)
  restart   Restart running container (code hot-mount only; no volume refresh)
  build     Build image
  logs      Tail container logs (-f)
  health    HTTP health checks (container + nginx proxy)
  compose   Pass-through to datalab-compose.sh (e.g. ops.sh compose ps)

Examples:
  ./data-lab-platform/embodied-annotate/ops.sh recreate
  ./data-lab-platform/embodied-annotate/ops.sh compose up -d embodied-annotate
EOF
}

cmd="${1:-help}"
shift || true

case "$cmd" in
  up)
    exec "$COMPOSE" up -d "$SERVICE" nginx "$@"
    ;;
  recreate)
    exec "$COMPOSE" up -d --force-recreate "$SERVICE" "$@"
    ;;
  restart)
    exec docker restart "$CONTAINER" "$@"
    ;;
  build)
    exec "$COMPOSE" build "$SERVICE" "$@"
    ;;
  logs)
    exec docker logs -f "$CONTAINER" "$@"
    ;;
  health)
    set +e
    docker exec "$CONTAINER" curl -fsS -o /dev/null -w "container:%{http_code}\n" http://127.0.0.1:7861/
    curl -fsS -o /dev/null -w "proxy:%{http_code}\n" "${HOST}/lerobot-annotate/"
    docker exec "$CONTAINER" test -f /app/docs/schemas/pick_place_v1.annotation_schema.json \
      && echo "schema-mount:ok" || echo "schema-mount:MISSING"
    ;;
  compose)
    exec "$COMPOSE" "$@"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage >&2
    exit 1
    ;;
esac
