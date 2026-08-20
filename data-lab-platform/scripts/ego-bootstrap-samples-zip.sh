#!/usr/bin/env bash
# Package stream LeRobot dataset into data-storage/samples/<slug>.zip for pipeline deploy.
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STREAM="${ROOT}/data-storage/stream/${STATION}"
SAMPLES="${ROOT}/data-storage/samples"
SLUG="${SAMPLES_SLUG:-$(python3 "${SCRIPT_DIR}/ego-pipeline-sessions.py" slug "${STATION}" --datalab-root "${ROOT}" 2>/dev/null || echo egodome)}"
OUT="${SAMPLES}/${SLUG}.zip"

log() { echo "[bootstrap-samples] $*"; }
die() { echo "[bootstrap-samples] ERROR: $*" >&2; exit 1; }

[[ -d "${STREAM}/data" ]] || die "missing ${STREAM}/data — derive READY first"
[[ -f "${STREAM}/meta/info.json" ]] || die "missing ${STREAM}/meta/info.json"

mkdir -p "${SAMPLES}"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

log "packaging ${STATION} → ${OUT}"
(
  cd "${STREAM}"
  for dir in data meta videos sensor_raw; do
    [[ -d "${dir}" ]] && cp -a "${dir}" "${tmp}/"
  done
)
(
  cd "${tmp}"
  zip -qr "${OUT}" data meta videos sensor_raw 2>/dev/null || zip -qr "${OUT}" data meta videos
)
log "wrote ${OUT} ($(du -h "${OUT}" | awk '{print $1}'))"
