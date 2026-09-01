#!/usr/bin/env bash
# Bootstrap ego-mcap-pilot stream meta for ego-run-pipeline (camera intrinsics, etc.).
# Pilot reuses ego-001 OAK rig intrinsics until per-station EEPROM export exists.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${STATION_ID:-ego-mcap-pilot}"
PROFILE="${EGO_MCAP_INTRINSICS_PROFILE:-production}"
SRC_STATION="${EGO_MCAP_INTRINSICS_SOURCE:-ego-001}"
STREAM="${ROOT}/data-storage/stream/${STATION}"
META="${STREAM}/meta"
FIXTURE_INTR="${ROOT}/data-lab-platform/fixtures/mcap/camera_intrinsics.golden-64x64.json"
SRC_INTR="${ROOT}/data-storage/stream/${SRC_STATION}/meta/camera_intrinsics.json"
DST_INTR="${META}/camera_intrinsics.json"
FORCE="${EGO_MCAP_INTRINSICS_FORCE:-0}"

case "${PROFILE}" in
  golden|golden-fixture)
    SRC_INTR="${FIXTURE_INTR}"
    ;;
  production|*)
    ;;
esac

mkdir -p "${META}"

if [[ ! -f "${DST_INTR}" || "${FORCE}" == "1" ]]; then
  if [[ -f "${SRC_INTR}" ]]; then
    cp "${SRC_INTR}" "${DST_INTR}"
    echo "[mcap-pilot-bootstrap] installed camera_intrinsics.json (profile=${PROFILE})"
  else
    echo "[mcap-pilot-bootstrap] WARN: missing ${SRC_INTR} — ego-process convert may fail until intrinsics deployed" >&2
    exit 1
  fi
else
  echo "[mcap-pilot-bootstrap] camera_intrinsics.json already present (use EGO_MCAP_INTRINSICS_FORCE=1 to replace)"
fi

echo "[mcap-pilot-bootstrap] OK: ${DST_INTR}"
