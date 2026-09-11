#!/usr/bin/env bash
# Fast 34-side reliability stress: manual derive + legacy gates (EGO_DERIVE_COMMERCIAL_GATE=0).
# No 130 capture. Order: quarantine junk → manual derive → repeat convert/sync.
set -euo pipefail

STATION="${STATION_ID:-ego-001}"
LOOPS="${STRESS_LOOPS:-2}"
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LOG="${DATALAB}/data-storage/logs/ego-stress-fast-$(date +%Y%m%d-%H%M%S).log"
SESSIONS_PY="${SCRIPT_DIR}/ego-pipeline-sessions.py"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "=== ego-stress-fast station=${STATION} loops=${LOOPS} ==="
log "log: ${LOG}"

log ">>> derive mode → manual (P-Ops-1)"
bash "${SCRIPT_DIR}/ego-derive-mode.sh" manual 2>&1 | tee -a "$LOG"

gate="$(docker exec data-lab-stream-ingest-1 printenv EGO_DERIVE_COMMERCIAL_GATE 2>/dev/null || echo "0")"
log "EGO_DERIVE_COMMERCIAL_GATE=${gate} (0=legacy tolerant)"

STREAM="${DATALAB}/data-storage/stream/${STATION}"
export EGO_USE_CONVERT_WORKER=0

log ">>> quarantine FAILED / exhausted retries (unblock convert)"
python3 "${SESSIONS_PY}" quarantine-failed "${STATION}" --apply --datalab-root "${DATALAB}" 2>&1 | tee -a "$LOG" || true
python3 <<PY 2>&1 | tee -a "$LOG"
from datetime import datetime, timezone
import json
from pathlib import Path
root = Path("${DATALAB}/data-storage/stream/${STATION}/state/sessions")
for sess_dir in sorted(root.glob("sess_*")):
    if not sess_dir.is_dir():
        continue
    if (sess_dir / "session.READY").is_file() or (sess_dir / "session.QUARANTINED").is_file():
        continue
    if not (sess_dir / "session.DONE_UPLOAD").is_file():
        continue
    payload = {
        "sessionId": sess_dir.name,
        "marker": "session.QUARANTINED",
        "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason": {"code": "STRESS_UNBLOCK", "message": "pending without READY", "category": "ops"},
    }
    (sess_dir / "session.QUARANTINED").write_text(json.dumps(payload, indent=2) + "\n")
    print("quarantined pending", sess_dir.name)
PY

log ">>> phase 1: manual derive + convert (ego-process --force-manual-derive)"
fail=0
t0=$SECONDS
if bash "${SCRIPT_DIR}/ego-process" "${STATION}" --force-manual-derive 2>&1 | tee -a "$LOG"; then
  log "phase1 OK elapsed=$((SECONDS - t0))s"
else
  log "phase1 FAIL elapsed=$((SECONDS - t0))s"
  fail=$((fail + 1))
fi

ready_n=$(find "${STREAM}/state/sessions" -name 'session.READY' 2>/dev/null | wc -l)
log "READY after derive=${ready_n}"

log ">>> phase 2: ${LOOPS}x ego-process --skip-derive (convert+sync+gate only)"
for i in $(seq 1 "${LOOPS}"); do
  t0=$SECONDS
  if bash "${SCRIPT_DIR}/ego-process" "${STATION}" --skip-derive 2>&1 | tee -a "$LOG"; then
    log "loop ${i}/${LOOPS} OK elapsed=$((SECONDS - t0))s"
  else
    log "loop ${i}/${LOOPS} FAIL elapsed=$((SECONDS - t0))s"
    fail=$((fail + 1))
  fi
done

failed_n=$(find "${STREAM}/state/sessions" -name 'session.FAILED' 2>/dev/null | wc -l)
quar_n=$(find "${STREAM}/state/sessions" -name 'session.QUARANTINED' 2>/dev/null | wc -l)
log "=== summary: fail=${fail} READY=${ready_n} FAILED=${failed_n} QUARANTINED=${quar_n} total_elapsed=$((SECONDS))s ==="
log "log: ${LOG}"
[[ "${fail}" -eq 0 ]] || exit 1
