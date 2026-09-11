#!/usr/bin/env bash
# Switch 34 derive between manual (P-Ops-1 gray) and async worker (P-Ops-2).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ego-production-defaults.sh
source "${SCRIPT_DIR}/ego-production-defaults.sh"
MODE_FILE="${ROOT}/data-lab-platform/.ego-derive-mode"
INGEST="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
WORKER="${DERIVE_WORKER_CONTAINER:-data-lab-derive-worker-1}"

TAG="${LEROBOT_IMAGE_TAG}"
IMAGE="${LEROBOT_IMAGE}"
OVERLAY_BASE="${EGO_COMPOSE_OVERLAY}"
OVERLAY_ASYNC="${EGO_COMPOSE_OVERLAY_ASYNC}"

COMPOSE=(docker compose)
if ! docker compose version &>/dev/null; then
  COMPOSE=(docker-compose)
fi

_compose_up() {
  local extra_overlay="${1:-}"
  local -a files=(
    -f docker-compose.yml
    -f data-lab-platform/docker-compose.platform.yml
    -f data-lab-platform/docker-compose.storage.override.yml
    -f "${OVERLAY_BASE}"
  )
  if [[ -n "$extra_overlay" ]]; then
    files+=(-f "$extra_overlay")
  fi
  (cd "${ROOT}" && "${COMPOSE[@]}" "${files[@]}" up -d --force-recreate stream-ingest derive-worker lerobot)
  local nginx_cid="${NGINX_CONTAINER:-data-lab-nginx-1}"
  if docker ps --format '{{.Names}}' | grep -q "^${nginx_cid}$"; then
    docker restart "${nginx_cid}" >/dev/null 2>&1 || true
    echo "nginx restarted (${nginx_cid})"
  fi
}

_usage() {
  cat <<EOF
Usage: $(basename "$0") manual|async|status

  manual   P-Ops-1: DERIVE_ASYNC=0, derive-worker stopped (ego-process 手动 derive)
  async    P-Ops-2: DERIVE_ASYNC=1, derive-worker 监听 DONE_UPLOAD
  status   当前模式、容器与环境变量

切换后 ego-process 会自动选择：
  manual → 批量 ego-derive run
  async  → 等待 derive-worker 将 session 派生至 READY
EOF
}

_set_mode_file() {
  printf '%s\n' "$1" > "${MODE_FILE}"
}

set_manual() {
  echo "==> derive mode: manual (P-Ops-1)"
  _compose_up ""
  docker stop "${WORKER}" >/dev/null 2>&1 || true
  _set_mode_file "manual"
  sleep 3
  echo "OK: DERIVE_ASYNC=0, ${WORKER} stopped"
  echo "    ego-process 将手动批量 derive"
}

set_async() {
  echo "==> derive mode: async (P-Ops-2)"
  _compose_up "${OVERLAY_ASYNC}"
  docker start "${WORKER}" >/dev/null 2>&1 || true
  _set_mode_file "async"
  sleep 5
  local da worker_state
  da="$(docker exec "${INGEST}" printenv DERIVE_ASYNC 2>/dev/null || echo "?")"
  worker_state="$(docker inspect "${WORKER}" --format '{{.State.Status}}' 2>/dev/null || echo missing)"
  echo "OK: DERIVE_ASYNC=${da}, ${WORKER}=${worker_state}"
  echo "    ego-process 将等待 derive-worker 完成派生"
}

show_status() {
  echo "=== derive mode ==="
  if [[ -f "${MODE_FILE}" ]]; then
    cat "${MODE_FILE}"
  else
    echo "(mode file unset — default: ${EGO_DERIVE_MODE_DEFAULT})"
  fi
  echo "  production defaults: manual + EGO_DERIVE_COMMERCIAL_GATE=${EGO_DERIVE_COMMERCIAL_GATE}"
  echo "=== stream-ingest ==="
  if docker ps --format '{{.Names}}' | grep -q "^${INGEST}$"; then
    docker exec "${INGEST}" printenv DERIVE_ASYNC DERIVE_ASYNC_EGO_001 DERIVE_LAYOUT DERIVE_STANDALONE 2>/dev/null \
      | sed 's/^/  /' || true
  else
    echo "  ${INGEST} not running"
  fi
  echo
  echo "=== derive-worker ==="
  docker ps -a --filter "name=^${WORKER}$" --format '  {{.Names}} {{.Status}}' 2>/dev/null || echo "  missing"
  echo
  echo "=== overlays ==="
  echo "  manual: ${OVERLAY_BASE}"
  echo "  async:  ${OVERLAY_ASYNC}"
}

MODE="${1:-}"
case "${MODE}" in
  manual) set_manual ;;
  async) set_async ;;
  status) show_status ;;
  -h|--help|"") _usage; [[ -n "${MODE}" ]] || exit 1 ;;
  *)
    echo "Unknown mode: ${MODE}" >&2
    _usage
    exit 1
    ;;
esac
