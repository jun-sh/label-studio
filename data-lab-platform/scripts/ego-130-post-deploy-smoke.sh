#!/usr/bin/env bash
# Post-deploy smoke on ego-130: stop any capture, verify idle, optional start/stop cycle.
# Usage: RC_CAPTURE_PASS=1 bash ego-130-post-deploy-smoke.sh [server@10.10.10.130]
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"

_run_remote() {
  local script="$1"
  if ssh -o BatchMode=yes -o ConnectTimeout=8 "${TARGET}" "bash -s" <<< "$script" 2>/dev/null; then
    return 0
  fi
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" TARGET="$TARGET" REMOTE_BODY="$script" python3 - <<'PY'
import os, paramiko, sys
target = os.environ["TARGET"]
user, _, host = target.partition("@")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=20)
_, stdout, stderr = c.exec_command(f"bash -s <<'REMOTE'\n{os.environ['REMOTE_BODY']}\nREMOTE")
sys.stdout.write(stdout.read().decode())
sys.stderr.write(stderr.read().decode())
raise SystemExit(stdout.channel.recv_exit_status())
PY
}

echo "==> Post-deploy smoke on ${TARGET}"

_run_remote "$(cat <<'REMOTE'
set -euo pipefail
systemctl --user stop ecs-oak-mcap-capture-stack.target 2>/dev/null || true
systemctl --user reset-failed ecs-record-oak-mcap.service 2>/dev/null || true
sleep 1
curl -sf http://127.0.0.1:8080/api/status | python3 -c "import sys,json; s=json.load(sys.stdin); assert s.get('state')=='idle', s; print('idle_ok')"
test -f ~/workspace/ego-studio/src/ego_capture_studio/capture/capture_stop_state.py
grep -q 'PHASE_WARM_IDLE' ~/workspace/ego-studio/src/ego_capture_studio/capture/capture_stop_state.py
PYTHONPATH=~/workspace/ego-studio/src ~/workspace/ego-studio/.venv/bin/python -c \
  "from ego_capture_studio.capture.capture_stop_state import stop_is_complete; print('import_ok')"
echo "post_deploy_smoke_ok"
REMOTE
)"

echo "==> Done."
