#!/usr/bin/env bash
# Single source of truth — EgoDome production image & compose overlays (v0.1.3+).
# Usage: source "$(dirname "$0")/ego-production-defaults.sh"

: "${EGO_PRODUCTION_TAG:=v0.1.3}"
: "${EGO_COMPOSE_OVERLAY:=data-lab-platform/docker-compose.v0.1.3.yml}"
: "${EGO_COMPOSE_OVERLAY_ASYNC:=data-lab-platform/docker-compose.v0.1.3-async.yml}"
: "${LEROBOT_IMAGE_TAG:=${EGO_PRODUCTION_TAG}}"
: "${LEROBOT_IMAGE:=data-lab-lerobot-studio:${EGO_PRODUCTION_TAG}}"

# Base compose files (without async overlay).
ego_compose_production_files() {
  printf '%s\n' \
    docker-compose.yml \
    data-lab-platform/docker-compose.platform.yml \
    data-lab-platform/docker-compose.storage.override.yml \
    "${EGO_COMPOSE_OVERLAY}"
}

# Append async overlay when derive mode is async (or when forced).
ego_compose_files_for_mode() {
  local mode="${1:-}"
  local root="${2:-}"
  if [[ -z "${mode}" && -n "${root}" && -f "${root}/data-lab-platform/.ego-derive-mode" ]]; then
    mode="$(tr -d '[:space:]' < "${root}/data-lab-platform/.ego-derive-mode")"
  fi
  ego_compose_production_files
  if [[ "${mode}" == "async" ]]; then
    printf '%s\n' "${EGO_COMPOSE_OVERLAY_ASYNC}"
  fi
}
