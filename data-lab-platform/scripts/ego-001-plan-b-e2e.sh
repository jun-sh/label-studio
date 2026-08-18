#!/usr/bin/env bash
# ego-001 Plan B automated gate: 34 deploy → 130 provision → capture API → upload → derive READY.
#
# Usage:
#   bash data-lab-platform/scripts/ego-001-plan-b-e2e.sh
#   SKIP_DEPLOY=1 SKIP_CLEAN=1 bash ...   # reuse existing stack/data
#
# Env: RC_CAPTURE_HOST RC_CAPTURE_USER RC_CAPTURE_PASS RC_STATION RECORD_HOLD_S
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${RC_STATION:-ego-001}"
CAPTURE_HOST="${RC_CAPTURE_HOST:-10.10.10.130}"
CAPTURE_USER="${RC_CAPTURE_USER:-server}"
CAPTURE_PASS="${RC_CAPTURE_PASS:-1}"
RECORD_HOLD_S="${RECORD_HOLD_S:-8}"
SKIP_DEPLOY="${SKIP_DEPLOY:-0}"
SKIP_CLEAN="${SKIP_CLEAN:-0}"
SKIP_CAPTURE="${SKIP_CAPTURE:-0}"
STREAM_ROOT="${STREAM_ROOT:-${ROOT}/data-storage/stream}"

export ROOT RC_STATION RC_CAPTURE_HOST RC_CAPTURE_USER="${CAPTURE_USER}" RC_CAPTURE_PASS="${CAPTURE_PASS}" STREAM_ROOT

log() { echo "[ego-001-e2e] $*"; }
die() { echo "[ego-001-e2e] FAIL: $*" >&2; exit 1; }

if [[ "${SKIP_DEPLOY}" != "1" ]]; then
  log "=== deploy v0.0.11.2 on 34 ==="
  bash "${ROOT}/data-lab-platform/scripts/deploy-stream-ingest-v0.0.11.2.sh"
fi

if [[ "${SKIP_CLEAN}" != "1" ]]; then
  log "=== clean 34 stream layer station=${STATION} ==="
  STREAM_HOST="${STREAM_ROOT}/${STATION}"
  rm -rf "${STREAM_HOST}/videos" "${STREAM_HOST}/data" "${STREAM_HOST}/meta" \
    "${STREAM_HOST}/live" "${STREAM_HOST}/raw" "${STREAM_HOST}/state" 2>/dev/null || true
  mkdir -p "${STREAM_HOST}"
fi

log "=== provision 130 (${CAPTURE_HOST}) ==="
export PROVISION_TARGET="${CAPTURE_USER}@${CAPTURE_HOST}" PROVISION_STATION="${STATION}" \
  RC_CAPTURE_PASS="${CAPTURE_PASS}"
python3 "${ROOT}/data-lab-platform/scripts/ego-130-provision-paramiko.py"

log "=== deploy ego-web on 130 ==="
export ROOT RC_CAPTURE_HOST RC_CAPTURE_USER RC_CAPTURE_PASS
python3 - <<'PY'
import os, paramiko, sys
host = os.environ["RC_CAPTURE_HOST"]
user = os.environ["RC_CAPTURE_USER"]
password = os.environ["RC_CAPTURE_PASS"]
root = os.environ["ROOT"]

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=password, timeout=20)

def run(cmd, timeout=300):
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = (o.read() + e.read()).decode()
    code = o.channel.recv_exit_status()
    if code != 0:
        print(out, file=sys.stderr)
        sys.exit(code)
    return out

# rsync ego_web via scp paramiko SFTP
import pathlib
sftp = c.open_sftp()
dest = "/home/server/ego-web"
run(f"mkdir -p {dest}")
for rel in ["ego_web.py", "export_offline.py"]:
    local = pathlib.Path(root) / "data-lab-platform/ego-local-web" / rel
    sftp.put(str(local), f"{dest}/{rel}")
sftp.close()

run("systemctl --user restart ecs-ego-web.service 2>/dev/null || true")
print("ego-web restarted")
c.close()
PY

if [[ "${SKIP_CAPTURE}" != "1" ]]; then
  log "=== capture via ego-web API hold=${RECORD_HOLD_S}s ==="
  export RC_CAPTURE_HOST RC_CAPTURE_USER RC_CAPTURE_PASS RECORD_HOLD_S
  python3 - <<'PY'
import json, os, sys, time, urllib.error, urllib.request

host = os.environ["RC_CAPTURE_HOST"]
hold = float(os.environ.get("RECORD_HOLD_S", "8"))
base = f"http://{host}:8080"

def req(path, method="GET", timeout=120):
    r = urllib.request.Request(f"{base}{path}", method=method, data=b"" if method != "GET" else None)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as err:
        body = err.read().decode()
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"msg": body}
        raise RuntimeError(f"{method} {path} -> HTTP {err.code}: {payload.get('msg', body)}") from err

def status():
    return req("/api/status")

def wait_idle(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if status().get("state") == "idle":
            return
        time.sleep(0.5)
    sys.exit("timeout waiting idle")

# wait web up
for _ in range(30):
    try:
        s = status()
        break
    except Exception:
        time.sleep(2)
else:
    sys.exit("ego-web /api/status unreachable")

time.sleep(12)  # MIN_ACTION_INTERVAL on ego-web
wait_idle(120)

try:
    req("/api/capture/start", "POST", timeout=120)
except RuntimeError as err:
    if "HTTP 409" in str(err):
        req("/api/capture/stop", "POST", timeout=120)
        wait_idle(120)
        req("/api/capture/start", "POST", timeout=120)
    else:
        raise
deadline = time.time() + 90
while time.time() < deadline:
    st = status().get("state")
    if st == "recording":
        break
    if st == "error":
        sys.exit("capture error: " + json.dumps(status()))
    time.sleep(0.5)
else:
    sys.exit("timeout waiting recording")

time.sleep(hold)
req("/api/capture/stop", "POST", timeout=120)
wait_idle(90)
print("capture idle ok")
PY
fi

log "=== verify h264 on 130 ==="
export RC_CAPTURE_PASS="${CAPTURE_PASS}"
python3 "${ROOT}/data-lab-platform/scripts/ego-130-verify-h264-paramiko.py" "${CAPTURE_USER}@${CAPTURE_HOST}"

log "=== verify latest segment has 4x .h264 ==="
export RC_CAPTURE_HOST RC_CAPTURE_USER RC_CAPTURE_PASS RC_STATION
CAPTURE_SESSION="$(python3 - <<'PY'
import os, paramiko, sys
host = os.environ["RC_CAPTURE_HOST"]
user = os.environ["RC_CAPTURE_USER"]
password = os.environ["RC_CAPTURE_PASS"]
station = os.environ["RC_STATION"]
seg_root = f"/home/server/cache/{station}/segments"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=password, timeout=20)
_, o, _ = c.exec_command(
    f"ls -td {seg_root}/sessions/sess_*/segments/seg_* 2>/dev/null | head -1", timeout=30
)
seg = o.read().decode().strip()
if not seg:
    sys.exit("no segment dir on 130")
_, o, _ = c.exec_command(f"ls {seg}/streams/*.h264 2>/dev/null | wc -l", timeout=30)
n = int(o.read().decode().strip() or "0")
if n < 4:
    sys.exit(f"expected 4 h264 streams, got {n} in {seg}")
print(f"segment h264 ok: {seg} ({n} streams)", file=sys.stderr)
session = seg.split("/sessions/")[1].split("/")[0]
print(session)
c.close()
PY
)"
[[ -n "${CAPTURE_SESSION}" ]] || die "could not discover capture session on 130"

log "=== RC upload + derive READY session=${CAPTURE_SESSION} ==="
RC_STATION="${STATION}" RC_SESSION="${CAPTURE_SESSION}" RC_CAPTURE_HOST="${CAPTURE_HOST}" \
  RC_CAPTURE_USER="${CAPTURE_USER}" RC_CAPTURE_PASS="${CAPTURE_PASS}" RC_UPLOAD_LIMIT=1 RC_ASSERT_READY=1 \
  bash "${ROOT}/data-lab-platform/scripts/rc-e2e-upload.sh"

log "=== validate info.json four RGB keys ==="
export RC_STATION STREAM_ROOT
python3 - <<'PY'
import json, os, sys
from pathlib import Path
station = os.environ["RC_STATION"]
root = Path(os.environ["STREAM_ROOT"]) / station
info = json.loads((root / "meta/info.json").read_text())
feats = info.get("features", {})
want = [
    "observation.images.camera_front_left",
    "observation.images.camera_front_right",
    "observation.images.camera_rear_left",
    "observation.images.camera_rear_right",
]
for k in want:
    if k not in feats:
        sys.exit(f"missing feature {k}")
if "observation.images.camera_depth_left" in feats:
    sys.exit("camera_depth_left should not be primary in ego-standard")
intr = json.loads((root / "meta/camera_intrinsics.json").read_text())
tid = intr.get("topology", {}).get("topology_id")
if tid != "ego-standard":
    sys.exit(f"topology_id={tid} expected ego-standard")
print("info.json + intrinsics topology ok")
PY

log "=== platform CI gate ==="
RC_STATION="${STATION}" bash "${ROOT}/data-lab-platform/scripts/ci-ego-platform.sh"

log "=== ego-001 Plan B E2E PASSED ==="
log "Manual: open http://${CAPTURE_HOST}:8080 on phone and repeat capture → collection viewer on 34"
