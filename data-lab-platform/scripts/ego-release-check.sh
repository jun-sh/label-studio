#!/usr/bin/env bash
# P0 release gate: derive SSOT, pipeline automation, convert worker, 130 pilot profile.
# Usage: ego-release-check.sh [station] [ssh_target]
#   EGO_RELEASE_SKIP_130=1   skip 130 SSH checks
#   EGO_RELEASE_SKIP_DOCKER=1 skip stream-ingest container checks
set -euo pipefail

STATION="${1:-ego-001}"
TARGET="${2:-server@10.10.10.130}"
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PROFILE="${DATALAB_ROOT}/data-lab-platform/config/station-profiles/${STATION}-mcap-production.env"
SESSIONS_PY="${SCRIPT_DIR}/ego-pipeline-sessions.py"
COMPOSE=(docker-compose -f "${DATALAB_ROOT}/docker-compose.yml" -f "${DATALAB_ROOT}/data-lab-platform/docker-compose.platform.yml")

fail=0
die() { echo "FAIL: $*" >&2; fail=1; }
ok() { echo "OK: $*"; }

echo "==> ego-release-check station=${STATION}"

# --- P0-1 derive SSOT (34) ---
if [[ "${EGO_RELEASE_SKIP_DOCKER:-0}" != "1" ]]; then
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'data-lab-stream-ingest-1'; then
    if docker exec data-lab-stream-ingest-1 grep -q 'sliceAnnexBFromFirstIdr' /app/derive/unit.mjs 2>/dev/null; then
      ok "stream-ingest unit.mjs IDR concat"
    else
      die "stream-ingest missing sliceAnnexBFromFirstIdr — recreate stream-ingest with derive bind-mount"
    fi
    if docker exec data-lab-stream-ingest-1 test -f /app/scripts/merge-station-imu.py 2>/dev/null; then
      ok "stream-ingest merge-station-imu.py"
    else
      die "stream-ingest missing merge-station-imu.py"
    fi
  else
    die "data-lab-stream-ingest-1 not running"
  fi
else
  ok "docker checks skipped (EGO_RELEASE_SKIP_DOCKER=1)"
fi

# --- P0 L2/L3 product path (34 daily hot path) ---
if [[ -f "$PROFILE" ]]; then
  grep -q '^EGO_OAK_MODE=passthrough' "$PROFILE" && ok "profile EGO_OAK_MODE=passthrough (L2)" \
    || die "profile missing EGO_OAK_MODE=passthrough"
  grep -q '^EGO_USE_CONVERT_WORKER=0' "$PROFILE" && ok "profile EGO_USE_CONVERT_WORKER=0 (WiLoR off daily path)" \
    || die "profile missing EGO_USE_CONVERT_WORKER=0"
  grep -q '^EGO_NOTIFY_PROCESS=1' "$PROFILE" && ok "profile EGO_NOTIFY_PROCESS=1" \
    || die "profile missing EGO_NOTIFY_PROCESS=1"
  [[ -x "${SCRIPT_DIR}/ego-lerobot-purity-gate.sh" ]] && ok "ego-lerobot-purity-gate.sh present" \
    || die "missing ego-lerobot-purity-gate.sh"
  [[ -x "${SCRIPT_DIR}/ego-viewer-preview" ]] && ok "ego-viewer-preview present" \
    || die "missing ego-viewer-preview"
  [[ -x "${SCRIPT_DIR}/ego-delivery-run" ]] && ok "ego-delivery-run present" \
    || die "missing ego-delivery-run"
else
  die "missing profile ${PROFILE}"
fi

# --- P0-3 pipeline automation ---
if systemctl is-active data-lab-ego-process-watcher.timer >/dev/null 2>&1 \
  || systemctl --user is-active data-lab-ego-process-watcher.timer >/dev/null 2>&1; then
  ok "ego-process-watcher timer active"
else
  die "ego-process-watcher timer not active (install-ego-process-watcher-systemd.sh)"
fi

if python3 "$SESSIONS_PY" doctor "$STATION" --datalab-root "$DATALAB_ROOT" >/dev/null 2>&1; then
  ok "pipeline markers clean"
else
  die "stale pipeline markers — ego-pipeline-sessions.py reconcile-markers ${STATION} --apply"
fi

# --- P0-4 convert worker health file writable ---
HB="${DATALAB_ROOT}/data-storage/pipeline/.convert-worker-${STATION}.heartbeat"
mkdir -p "$(dirname "$HB")"
if [[ -w "$(dirname "$HB")" ]]; then
  ok "convert worker heartbeat dir writable"
else
  die "pipeline dir not writable: $(dirname "$HB")"
fi

# --- P0-2 130 pilot SSOT ---
if [[ "${EGO_RELEASE_SKIP_130:-0}" != "1" ]]; then
  if bash "${SCRIPT_DIR}/ego-station-doctor.sh" "$STATION" "$TARGET" >/dev/null 2>&1; then
    ok "130 + 34 station-doctor PASS"
  else
    RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" bash "${SCRIPT_DIR}/ego-station-doctor.sh" "$STATION" "$TARGET" || fail=1
  fi
else
  ok "130 checks skipped (EGO_RELEASE_SKIP_130=1)"
fi

if [[ "$fail" -eq 0 ]]; then
  echo "==> RELEASE CHECK PASS"
  exit 0
fi
echo "==> RELEASE CHECK FAIL"
exit 1
