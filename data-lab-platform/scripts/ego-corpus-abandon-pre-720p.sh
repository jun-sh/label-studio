#!/usr/bin/env bash
# Archive pre-720p corpus/viewer artifacts (rename, never rm -rf data-storage/corpus).
# Usage: ego-corpus-abandon-pre-720p.sh [corpus_slug]
set -euo pipefail

SLUG="${1:-ego_001}"
STATION="${EGO_STATION:-ego-001}"
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STORAGE="${DATALAB_ROOT}/data-storage"
STAMP="$(date +%Y%m%d-%H%M%S)"

log() { echo "[corpus-abandon] $*"; }

_guard() {
  case "$1" in
    "${STORAGE}/corpus/${SLUG}"|"${STORAGE}/samples/${SLUG}"|"${STORAGE}/samples/${SLUG}.zip") ;;
    *) echo "refusing path outside corpus/samples slug: $1" >&2; exit 2 ;;
  esac
}

_move_if_exists() {
  local src="$1"
  local dst="$2"
  _guard "$src"
  if [[ -e "$src" ]]; then
    if [[ -e "$dst" ]]; then
      echo "destination exists: $dst" >&2
      exit 2
    fi
    mv "$src" "$dst"
    log "archived $src -> $dst"
  fi
}

CORPUS="${STORAGE}/corpus/${SLUG}"
SAMPLES_DIR="${STORAGE}/samples/${SLUG}"
SAMPLES_ZIP="${STORAGE}/samples/${SLUG}.zip"

_move_if_exists "$CORPUS" "${CORPUS}.abandoned-pre720p-${STAMP}"
_move_if_exists "$SAMPLES_DIR" "${SAMPLES_DIR}.abandoned-pre720p-${STAMP}"
_move_if_exists "$SAMPLES_ZIP" "${SAMPLES_ZIP}.abandoned-pre720p-${STAMP}"

for overlay in "${STORAGE}/samples/${SLUG}_hand_kp2d.json" \
  "${STORAGE}/samples/${SLUG}_depth_preview.json"; do
  if [[ -f "$overlay" ]]; then
    mv "$overlay" "${overlay}.abandoned-pre720p-${STAMP}"
    log "archived $overlay"
  fi
done

FRAMES="${STORAGE}/samples/${SLUG}_depth_preview_frames"
if [[ -d "$FRAMES" ]]; then
  mv "$FRAMES" "${FRAMES}.abandoned-pre720p-${STAMP}"
  log "archived $FRAMES"
fi

mkdir -p "$CORPUS/meta" "$STORAGE/samples/${SLUG}/dataset/meta"
log "ready for fresh rectified 720p corpus (station=${STATION} slug=${SLUG})"
