#!/usr/bin/env bash
# fix2 grayscale P0 preflight: deploy config, derive health, 4 core metrics, Viewer gate.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
STREAM="${ROOT}/data-storage/stream/${STATION}"
INGEST="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
HOST="${LABEL_STUDIO_HOST:-http://127.0.0.1:8080}"
HOST="${HOST%/}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=ego-production-defaults.sh
source "${SCRIPT_DIR}/ego-production-defaults.sh"
OVERLAY="${EGO_COMPOSE_OVERLAY}"
SAMPLES_ZIP="${ROOT}/data-storage/samples/$(python3 "${ROOT}/data-lab-platform/scripts/ego-pipeline-sessions.py" slug "${STATION}" --datalab-root "${ROOT}" 2>/dev/null || echo egodome).zip"

PASS=0
FAIL=0
WARN=0

log() { echo "[grayscale-preflight] $*"; }
ok() { log "PASS: $*"; PASS=$((PASS + 1)); }
bad() { log "FAIL: $*"; FAIL=$((FAIL + 1)); }
warn() { log "WARN: $*"; WARN=$((WARN + 1)); }

log "=== fix2 grayscale P0 preflight (${STATION}) ==="

# --- deploy config ---
if docker ps --format '{{.Names}}' | grep -q "^${INGEST}$"; then
  img="$(docker inspect "${INGEST}" --format '{{.Config.Image}}')"
  [[ "${img}" == *":${EGO_PRODUCTION_TAG}"* || "${img}" == *":v0.1.0"* || "${img}" == *"v0.0.13"* ]] \
    && ok "image ${img}" || bad "image not production (${EGO_PRODUCTION_TAG}): ${img}"

  se="$(docker exec "${INGEST}" printenv STREAM_SESSION_SINGLE_EPISODE 2>/dev/null || echo "?")"
  [[ "${se}" == "0" ]] && ok "STREAM_SESSION_SINGLE_EPISODE=0" || bad "STREAM_SESSION_SINGLE_EPISODE=${se} (expect 0)"

  da="$(docker exec "${INGEST}" printenv DERIVE_ASYNC 2>/dev/null || echo "?")"
  [[ "${da}" == "1" ]] && ok "DERIVE_ASYNC=1 (production async)" || warn "DERIVE_ASYNC=${da} (fix2 gray expected manual=0; production uses async=1)"

  dl="$(docker exec "${INGEST}" printenv DERIVE_LAYOUT 2>/dev/null || echo "?")"
  [[ "${dl}" == "unit" ]] && ok "DERIVE_LAYOUT=unit" || warn "DERIVE_LAYOUT=${dl} (legacy path)"

  docker exec "${INGEST}" python3 -c "
import re
t=open('/app/derive/imu/ingest-raw.py').read()
assert int(re.search(r'PAIR_TOLERANCE_NS\s*=\s*([\d_]+)', t).group(1).replace('_',''))==3000000
" >/dev/null 2>&1 && ok "IMU PAIR_TOLERANCE_NS=3ms" || bad "IMU tolerance check failed"
else
  bad "container ${INGEST} not running"
fi

if docker ps -a --format '{{.Names}}\t{{.Status}}' | grep -q '^data-lab-derive-worker-1.*Exited'; then
  ok "derive-worker stopped (manual ego-derive run mode)"
elif ! docker ps --format '{{.Names}}' | grep -q '^data-lab-derive-worker-1$'; then
  ok "derive-worker not running (manual mode)"
else
  warn "derive-worker is running — grayscale P0 expects manual ego-derive run"
fi

[[ -f "${ROOT}/${OVERLAY}" ]] && ok "overlay present: ${OVERLAY}" || bad "missing overlay ${OVERLAY}"
[[ -f "${ROOT}/data-lab-platform/docker-compose.v0.0.13-fix1.yml" ]] && ok "rollback overlay fix1 available" || warn "rollback overlay fix1 missing"

# --- egodome.zip ---
[[ -f "${SAMPLES_ZIP}" ]] && ok "samples zip: ${SAMPLES_ZIP}" || bad "missing ${SAMPLES_ZIP}"

# --- deriver.lock ---
if [[ -f "${STREAM}/state/deriver.lock" ]]; then
  bad "deriver.lock present (stale derive?)"
else
  ok "no deriver.lock"
fi

# --- session.READY (gate 3/3 unit or 6/6 legacy) ---
ready_n=0
failed_n=0
for d in "${STREAM}/state/sessions"/sess_*; do
  [[ -d "${d}" ]] || continue
  sid="$(basename "${d}")"
  if [[ -f "${d}/session.READY" ]]; then
    gate="$(python3 -c "import json; print(json.load(open('${d}/session.READY')).get('ready_gate',{}).get('checks_passed','?'))" 2>/dev/null || echo "?")"
    total="$(python3 -c "import json; print(json.load(open('${d}/session.READY')).get('ready_gate',{}).get('checks_total','?'))" 2>/dev/null || echo "?")"
    if [[ "${gate}" == "${total}" && "${gate}" != "?" && "${gate}" != "0" ]]; then
      ok "session.READY ${sid} (${gate}/${total})"
      ready_n=$((ready_n + 1))
    else
      bad "session.READY ${sid} gate ${gate}/${total}"
    fi
  elif [[ -f "${d}/session.FAILED" ]]; then
    bad "session.FAILED ${sid}"
    failed_n=$((failed_n + 1))
  fi
done
[[ "${ready_n}" -gt 0 ]] && ok "derive success: ${ready_n} READY" || bad "no session.READY found"
[[ "${failed_n}" -eq 0 ]] || bad "derive failures: ${failed_n}"

# --- manifest (unit layout) ---
if [[ -f "${STREAM}/manifest/manifest.json" ]]; then
  mf="$(python3 -c "import json; m=json.load(open('${STREAM}/manifest/manifest.json')); print(m.get('total_frames','?'), len(m.get('episodes',[])))")"
  ok "manifest present (${mf})"
fi

# --- G6 IMU null rate (sample derived units or legacy jsonl) ---
g6_sample=""
if compgen -G "${STREAM}/derived/sess_*/data.jsonl" >/dev/null; then
  g6_sample="$(ls "${STREAM}/derived"/sess_*/data.jsonl | head -1)"
elif [[ -f "${STREAM}/data/chunk-000/file-000.jsonl" ]]; then
  g6_sample="${STREAM}/data/chunk-000/file-000.jsonl"
fi
if [[ -n "${g6_sample}" ]]; then
  g6="$(python3 <<PY
import json
from pathlib import Path
miss = tot = 0
for line in Path("${g6_sample}").read_text().splitlines():
    if not line.strip(): continue
    r = json.loads(line)
    for k in ("observation.imu_accel", "observation.imu_gyro"):
        tot += 1
        v = r.get(k)
        if v is None or (isinstance(v, list) and any(x is None for x in v)): miss += 1
    tot += 1
    if r.get("observation.imu_timestamp") is None: miss += 1
print(f"{100*miss/max(1,tot):.4f}")
PY
)"
  if python3 -c "exit(0 if float('${g6}') < 1.0 else 1)"; then
    ok "G6 IMU null rate ${g6}% (<1%)"
  else
    bad "G6 IMU null rate ${g6}% (>=1%)"
  fi
else
  bad "missing IMU sample jsonl (derived unit or L2 chunk-000)"
fi

# --- Viewer stream ---
code="$(curl -s -o /tmp/preflight-viewer-info.json -w '%{http_code}' --max-time 10 "${HOST}/lerobot/api/stream/${STATION}/meta/info.json" 2>/dev/null || echo 000)"
if [[ "${code}" == "200" ]]; then
  vf="$(python3 -c "import json; i=json.load(open('/tmp/preflight-viewer-info.json')); print(i.get('total_frames','?'), i.get('total_episodes','?'))" 2>/dev/null || echo "? ?")"
  ok "Viewer stream info.json HTTP ${code} (${vf})"
else
  bad "Viewer stream info.json HTTP ${code}"
fi

raw_n="$(find "${STREAM}/raw/segments" -name '*.tar.zst' 2>/dev/null | wc -l | tr -d ' ')"
ok "raw archives preserved: ${raw_n}"

log "=== summary: PASS=${PASS} FAIL=${FAIL} WARN=${WARN} ==="
if [[ "${FAIL}" -gt 0 ]]; then
  log "P0 preflight FAILED — fix issues before gray trial capture"
  exit 1
fi
log "P0 preflight PASSED — ready for grayscale capture SOP"
exit 0
