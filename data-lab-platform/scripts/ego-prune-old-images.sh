#!/usr/bin/env bash
# Prune old data-lab-lerobot-studio images; keep production + one rollback tag.
#
# Usage:
#   bash ego-prune-old-images.sh           # dry-run (default)
#   bash ego-prune-old-images.sh --apply   # delete listed tags
#
# Keeps: v0.1.1 (production), v0.1.0 (rollback). Override with KEEP_TAGS=...
set -euo pipefail

IMAGE_REPO="${LEROBOT_IMAGE_REPO:-data-lab-lerobot-studio}"
KEEP_TAGS="${KEEP_TAGS:-v0.1.2 v0.1.1}"
APPLY=0

for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    -h|--help)
      sed -n '2,10p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

log() { echo "[prune-images] $*"; }

mapfile -t ALL_TAGS < <(docker images "${IMAGE_REPO}" --format '{{.Tag}}' 2>/dev/null | sort -u || true)
if [[ ${#ALL_TAGS[@]} -eq 0 ]]; then
  log "no local tags for ${IMAGE_REPO}"
  exit 0
fi

is_kept() {
  local tag="$1"
  for k in ${KEEP_TAGS}; do
    [[ "${tag}" == "${k}" ]] && return 0
  done
  return 1
}

TO_DELETE=()
for tag in "${ALL_TAGS[@]}"; do
  is_kept "${tag}" || TO_DELETE+=("${IMAGE_REPO}:${tag}")
done

log "keep: ${KEEP_TAGS}"
log "all tags (${#ALL_TAGS[@]}): ${ALL_TAGS[*]}"
if [[ ${#TO_DELETE[@]} -eq 0 ]]; then
  log "nothing to prune"
  exit 0
fi

log "would remove (${#TO_DELETE[@]}):"
printf '  %s\n' "${TO_DELETE[@]}"

if [[ "${APPLY}" -eq 0 ]]; then
  log "dry-run — re-run with --apply to delete"
  exit 0
fi

for ref in "${TO_DELETE[@]}"; do
  if docker rmi "${ref}" 2>/dev/null; then
    log "removed ${ref}"
  else
    log "skip ${ref} (in use or already gone)"
  fi
done

log "remaining:"
docker images "${IMAGE_REPO}" --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' || true
