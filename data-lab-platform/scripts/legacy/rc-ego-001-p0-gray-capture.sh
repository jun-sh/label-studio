#!/usr/bin/env bash
# P0 grayscale: one real capture → upload → manual derive → preflight → log.
# Usage:
#   bash rc-ego-001-p0-gray-capture.sh              # single trip
#   bash rc-ego-001-p0-gray-capture.sh --status       # show P0 progress
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
CAPTURE_HOST="${RC_CAPTURE_HOST:-10.10.10.130}"
CAPTURE_USER="${RC_CAPTURE_USER:-server}"
CAPTURE_PASS="${RC_CAPTURE_PASS:-1}"
RECORD_HOLD_S="${RECORD_HOLD_S:-8}"
P0_TARGET="${P0_TARGET:-10}"
LOG="${P0_GRAY_LOG:-${ROOT}/data-lab-platform/.p0-gray-trial-log.jsonl}"
PREFLIGHT="${ROOT}/data-lab-platform/scripts/rc-ego-001-fix2-grayscale-preflight.sh"

log() { echo "[p0-gray] $*"; }
die() { echo "[p0-gray] ERROR: $*" >&2; exit 1; }

show_status() {
  python3 - "${LOG}" "${P0_TARGET}" <<'PY'
import json, sys
from pathlib import Path
log = Path(sys.argv[1])
target = int(sys.argv[2])
rows = []
if log.is_file():
    for line in log.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
ok = [r for r in rows if r.get("ready") and r.get("preflight_pass")]
print(f"P0 progress: {len(ok)}/{target} READY+preflight PASS")
for r in rows[-5:]:
    mark = "OK" if r.get("ready") and r.get("preflight_pass") else "FAIL"
    print(f"  [{mark}] trip={r.get('trip')} session={r.get('session_id','?')[:20]}… frames={r.get('frames','?')} g6={r.get('g6_null_pct','?')}% elapsed={r.get('elapsed_s','?')}s")
if len(ok) >= target:
    print("P0 EXIT CRITERIA MET")
PY
}

if [[ "${1:-}" == "--status" ]]; then
  show_status
  exit 0
fi

if [[ "${1:-}" == "--loop" ]]; then
  die "--loop disabled; run single-trip captures manually"
fi

docker stop data-lab-derive-worker-1 >/dev/null 2>&1 || true

trip="$(python3 -c "
from pathlib import Path
p=Path('${LOG}')
n=sum(1 for l in p.read_text().splitlines() if l.strip()) if p.is_file() else 0
print(n+1)
")"
log "=== P0 trip ${trip}/${P0_TARGET} ==="
t0=$(date +%s)

log "capture on ${CAPTURE_HOST} (hold=${RECORD_HOLD_S}s)"
export RC_CAPTURE_HOST="${CAPTURE_HOST}" RC_CAPTURE_USER="${CAPTURE_USER}" RC_CAPTURE_PASS="${CAPTURE_PASS}" RC_STATION="${STATION}" RECORD_HOLD_S
SESSION_ID="$(python3 - <<'PY'
import os, sys, time, urllib.request, urllib.error

host = os.environ["RC_CAPTURE_HOST"]
hold = float(os.environ.get("RECORD_HOLD_S", "8"))
base = f"http://{host}:8080"

def req(path, method="GET", timeout=120):
    r = urllib.request.Request(f"{base}{path}", method=method, data=b"" if method != "GET" else None)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        body = resp.read().decode()
        return __import__("json").loads(body) if body else {}

def status():
    return req("/api/status")

def wait_idle(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if status().get("state") == "idle":
            return
        time.sleep(0.5)
    sys.exit("timeout waiting idle")

wait_idle(120)
time.sleep(12)
try:
    req("/api/capture/start", "POST", timeout=120)
except urllib.error.HTTPError as err:
    if err.code == 409:
        req("/api/capture/stop", "POST", timeout=120)
        wait_idle(120)
        req("/api/capture/start", "POST", timeout=120)
    else:
        raise

deadline = time.time() + 90
while time.time() < deadline:
    if status().get("state") == "recording":
        break
    time.sleep(0.5)
else:
    sys.exit("timeout waiting recording")

time.sleep(hold)
req("/api/capture/stop", "POST", timeout=120)
wait_idle(120)

import paramiko
user = os.environ.get("RC_CAPTURE_USER", "server")
password = os.environ.get("RC_CAPTURE_PASS", "1")
station = os.environ.get("RC_STATION", "ego-001")
seg_root = f"/home/server/cache/{station}/segments"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=password, timeout=20)
_, o, _ = c.exec_command(f"ls -td {seg_root}/sessions/sess_*/segments/seg_* 2>/dev/null | head -1", timeout=30)
seg = o.read().decode().strip()
if not seg:
    sys.exit("no segment on 130 after capture")
session = seg.split("/sessions/")[1].split("/")[0]
print(session)
c.close()
PY
)"
[[ -n "${SESSION_ID}" ]] || die "capture produced no session"
log "session=${SESSION_ID}"

log "upload_segments"
RC_STATION="${STATION}" RC_SESSION="${SESSION_ID}" RC_UPLOAD_LIMIT=1 RC_ASSERT_READY=0 \
  RC_CAPTURE_HOST="${CAPTURE_HOST}" RC_CAPTURE_USER="${CAPTURE_USER}" RC_CAPTURE_PASS="${CAPTURE_PASS}" \
  bash "${ROOT}/data-lab-platform/scripts/rc-e2e-upload.sh"

log "manual derive (unit layout)"
docker stop data-lab-derive-worker-1 >/dev/null 2>&1 || true
DERIVE_LAYOUT=unit STATION_ID="${STATION}" bash "${ROOT}/data-lab-platform/scripts/ego-derive" run --session "${SESSION_ID}"

READY_PATH="${ROOT}/data-storage/stream/${STATION}/state/sessions/${SESSION_ID}/session.READY"
[[ -f "${READY_PATH}" ]] || die "session.READY missing for ${SESSION_ID}"

UNIT_JSON="${ROOT}/data-storage/stream/${STATION}/derived/${SESSION_ID}/unit.json"
[[ -f "${UNIT_JSON}" ]] || die "unit.json missing for ${SESSION_ID}"

metrics="$(python3 - "${READY_PATH}" "${UNIT_JSON}" <<'PY'
import json, sys
from pathlib import Path
ready = json.loads(Path(sys.argv[1]).read_text())
unit = json.loads(Path(sys.argv[2]).read_text())
jsonl = Path(sys.argv[2]).parent / "data.jsonl"
miss = tot = 0
if jsonl.is_file():
    for line in jsonl.read_text().splitlines():
        if not line.strip(): continue
        r = json.loads(line)
        for k in ("observation.imu_accel", "observation.imu_gyro"):
            tot += 1
            v = r.get(k)
            if v is None or (isinstance(v, list) and any(x is None for x in v)): miss += 1
        tot += 1
        if r.get("observation.imu_timestamp") is None: miss += 1
print(json.dumps({
    "frames": unit.get("frames") or ready.get("total"),
    "gate": ready.get("ready_gate", {}),
    "g6_null_pct": round(100*miss/max(1,tot), 4),
    "layout": "unit",
}))
PY
)"

log "preflight"
preflight_pass=0
if bash "${PREFLIGHT}"; then preflight_pass=1; fi

elapsed=$(( $(date +%s) - t0 ))
python3 - "${LOG}" <<PY
import json, datetime
from pathlib import Path
entry = {
    "trip": int("${trip}"),
    "session_id": "${SESSION_ID}",
    "at": datetime.datetime.utcnow().isoformat() + "Z",
    "ready": True,
    "preflight_pass": bool(${preflight_pass}),
    "elapsed_s": ${elapsed},
    **json.loads('''${metrics}'''),
}
with Path("${LOG}").open("a") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
print(json.dumps(entry, indent=2))
PY

show_status
[[ "${preflight_pass}" -eq 1 ]] || die "preflight failed trip ${trip}"
log "trip ${trip} OK (${SESSION_ID})"
