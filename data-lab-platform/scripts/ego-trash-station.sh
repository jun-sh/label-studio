#!/usr/bin/env bash
# Move an entire 34 stream station tree into trash (soft delete, recoverable by mv).
#
# Use this when collection/raw for a station must go away — NOT ego-reset-34-only.sh
# (derived-only; keeps raw/segments).
#
# Usage:
#   bash ego-trash-station.sh ego-002              # dry-run
#   bash ego-trash-station.sh ego-002 --apply
#   EGO_TRASH_YES=1 bash ego-trash-station.sh ego-002 --apply
#
# Restore (within retention window):
#   mv data-storage/_trash/stream/ego-002.<ts> data-storage/stream/ego-002
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STREAM_ROOT="${EGO_STREAM_ROOT:-${ROOT}/data-storage/stream}"
TRASH_ROOT="${EGO_TRASH_ROOT:-${ROOT}/data-storage/_trash/stream}"
AUDIT_LOG="${EGO_OPS_AUDIT_LOG:-${ROOT}/data-storage/audit/ego-ops.jsonl}"

STATION="${1:-}"
APPLY=0
[[ "${2:-}" == "--apply" ]] && APPLY=1

log() { echo "[ego-trash] $*"; }
die() { echo "[ego-trash] ERROR: $*" >&2; exit 1; }

usage() {
  sed -n '2,14p' "$0"
  exit 2
}

[[ -n "${STATION}" && "${STATION}" != "-h" && "${STATION}" != "--help" ]] || usage
[[ "${STATION}" =~ ^ego-[a-z0-9-]+$ ]] || die "invalid station id: ${STATION}"

SRC="${STREAM_ROOT}/${STATION}"
[[ -d "${SRC}" ]] || die "station path missing: ${SRC}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${TRASH_ROOT}/${STATION}.${TS}"

audit_line() {
  local action="$1"
  mkdir -p "$(dirname "${AUDIT_LOG}")"
  printf '{"ts":"%s","action":"%s","station":"%s","src":"%s","dest":"%s","user":"%s","host":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "${action}" \
    "${STATION}" \
    "${SRC}" \
    "${DEST}" \
    "${USER:-unknown}" \
    "$(hostname -s 2>/dev/null || echo unknown)" >>"${AUDIT_LOG}"
}

if [[ "${APPLY}" -ne 1 ]]; then
  log "dry-run: would mv ${SRC} -> ${DEST}"
  log "run with --apply to execute; restore: mv ${DEST} ${SRC}"
  exit 0
fi

if [[ "${EGO_TRASH_YES:-}" != "1" ]]; then
  echo "Move ENTIRE stream station (raw + derived + meta) to trash:"
  echo "  ${SRC}"
  echo "  -> ${DEST}"
  echo "Derived-only wipe (keeps raw): use ego-reset-34-only.sh instead."
  read -r -p "Continue? [y/N] " ans
  [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]] || die "aborted"
fi

mkdir -p "${TRASH_ROOT}"
mv "${SRC}" "${DEST}"
audit_line "trash_station"
log "moved to ${DEST}"
log "restore: mv ${DEST} ${SRC}"
