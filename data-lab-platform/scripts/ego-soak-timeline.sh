#!/usr/bin/env bash
# Timeline soak (Tier-1): consecutive 45s single-segment clips, stats at 5% (B tier).
# Does NOT fail the run on timeline 3–5% alone — reports corpus (3%) vs stats (5%) fail rates.
# Daily gate remains ego-stress-5min (2×45s + upload QC).
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
EPISODES="${SOAK_EPISODES:-20}"
RECORD_SECONDS="${SOAK_RECORD_SECONDS:-45}"
DURATIONS_CSV="${SOAK_DURATIONS_CSV:-$(python3 -c "print(','.join(['${RECORD_SECONDS}']*${EPISODES}))")}"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"
WAIT_AFTER_STOP="${E2E_WAIT_AFTER_STOP:-22}"

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LOG_DIR="${DATALAB}/data-storage/logs"
REPORT_DIR="${DATALAB}/data-lab-platform/docs/m0-evidence"
mkdir -p "$LOG_DIR" "$REPORT_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
RUN_LOG="${LOG_DIR}/soak-timeline-${STAMP}.log"
REPORT_JSON="${REPORT_DIR}/soak-timeline-${STAMP}.json"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RUN_LOG"; }

log "=== soak-timeline · ${STATION} · ${EPISODES}×${RECORD_SECONDS}s · stats tier 5% ==="
log "durations=${DURATIONS_CSV}"
log "log=${RUN_LOG}"

if [[ "${EGO_SOAK_SKIP_PROVISION:-0}" != "1" ]]; then
  unset EGO_STRESS_CAPTURE
  RC_CAPTURE_PASS="$EDGE_PASS" CAPTURE_PROFILE=pilot \
    bash "${SCRIPT_DIR}/ego-130-provision-mcap-production.sh" "server@10.10.10.130" "$STATION" 2>&1 | tee -a "$RUN_LOG"
fi

RC_CAPTURE_PASS="$EDGE_PASS" bash "${SCRIPT_DIR}/ego-release-check.sh" "$STATION" 2>&1 | tee -a "$RUN_LOG"

log ">>> record-only soak (preserve segments; 3% corpus vs 5% stats report)"
RC_CAPTURE_PASS="$EDGE_PASS" \
  EPISODES="$EPISODES" \
  STRESS_DURATIONS_CSV="$DURATIONS_CSV" \
  python3 "${SCRIPT_DIR}/ego-soak-timeline.py" 2>&1 | tee -a "$RUN_LOG" | tee /tmp/ego-soak-timeline-last.json

if [[ -f /tmp/ego-soak-timeline-last.json ]]; then
  cp /tmp/ego-soak-timeline-last.json "$REPORT_JSON"
fi

log "report=${REPORT_JSON}"
log "🎉 soak-timeline complete (see report for 3% vs 5% fail rates)"
