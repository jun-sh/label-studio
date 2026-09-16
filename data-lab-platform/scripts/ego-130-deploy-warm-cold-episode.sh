#!/usr/bin/env bash
# Deploy Warm USB + Cold Episode to ego-130 (code + systemd only; no full provision).
# Usage: RC_CAPTURE_PASS=1 bash ego-130-deploy-warm-cold-episode.sh [server@10.10.10.130]
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CAPTURE_SRC="${ROOT}/ego-stream-client"
EGO_WEB_SRC="${ROOT}/ego-local-web"
REMOTE_STUDIO="/home/server/workspace/ego-studio"
REMOTE_CAPTURE="${REMOTE_STUDIO}/src/ego_capture_studio/capture"
REMOTE_CLI="${REMOTE_STUDIO}/src/ego_capture_studio/cli"
REMOTE_WEB="/home/server/ego-web"

echo "==> Preflight (compile + CLI smoke)"
python3 -m py_compile \
  "${CAPTURE_SRC}/record_oak_stream.py" \
  "${CAPTURE_SRC}/oak_warm_session.py" \
  "${CAPTURE_SRC}/capture_stop_state.py" \
  "${CAPTURE_SRC}/async_frame_writer.py" \
  "${CAPTURE_SRC}/cli/record_oak_stream.py" \
  "${EGO_WEB_SRC}/ego_web.py"
CAPTURE_SRC_ABS="$(cd "${CAPTURE_SRC}" && pwd)"
python3 - <<PY
from pathlib import Path
cli = Path("${CAPTURE_SRC_ABS}/cli/record_oak_stream.py").read_text(encoding="utf-8")
assert "record_oak_stream import main" in cli, "cli must import capture.main"
assert 'run_name="__main__"' not in cli, "runpy __main__ breaks h264 backend patch"
print("cli_smoke_ok")
PY

echo "==> Deploy warm cold episode to ${TARGET}"

_scp() {
  local src="$1" dst="$2"
  if scp -q -o BatchMode=yes -o ConnectTimeout=8 "$src" "${TARGET}:${dst}" 2>/dev/null; then
    return 0
  fi
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" TARGET="$TARGET" SCP_SRC="$src" SCP_DST="$dst" python3 - <<'PY'
import os, paramiko
target = os.environ["TARGET"]
user, _, host = target.partition("@")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=20)
c.open_sftp().put(os.environ["SCP_SRC"], os.environ["SCP_DST"])
c.close()
print(f"scp ok {os.environ['SCP_DST']}")
PY
}

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
rc = stdout.channel.recv_exit_status()
c.close()
raise SystemExit(rc)
PY
}

echo "==> Sync capture modules"
_scp "${CAPTURE_SRC}/record_oak_stream.py" "/tmp/record_oak_stream.py"
_scp "${CAPTURE_SRC}/oak_warm_session.py" "/tmp/oak_warm_session.py"
_scp "${CAPTURE_SRC}/capture_stop_state.py" "/tmp/capture_stop_state.py"
_scp "${CAPTURE_SRC}/async_frame_writer.py" "/tmp/async_frame_writer.py"
_scp "${CAPTURE_SRC}/cli/record_oak_stream.py" "/tmp/cli_record_oak_stream.py"
_scp "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap.service.d/z-mcap-production.conf" "/tmp/z-mcap-production.conf"
_scp "${EGO_WEB_SRC}/ego_web.py" "/tmp/ego_web.py"
_scp "${EGO_WEB_SRC}/systemd/ecs-ego-web.service.d/z-mcap-production.conf" "/tmp/z-ego-web-mcap-production.conf"

_run_remote "$(cat <<'REMOTE'
set -euo pipefail
REMOTE_CAPTURE="/home/server/workspace/ego-studio/src/ego_capture_studio/capture"
REMOTE_CLI="/home/server/workspace/ego-studio/src/ego_capture_studio/cli"
REMOTE_WEB="/home/server/ego-web"
install -m 0644 /tmp/record_oak_stream.py "${REMOTE_CAPTURE}/record_oak_stream.py"
install -m 0644 /tmp/oak_warm_session.py "${REMOTE_CAPTURE}/oak_warm_session.py"
install -m 0644 /tmp/capture_stop_state.py "${REMOTE_CAPTURE}/capture_stop_state.py"
install -m 0644 /tmp/async_frame_writer.py "${REMOTE_CAPTURE}/async_frame_writer.py"
install -m 0644 /tmp/cli_record_oak_stream.py "${REMOTE_CLI}/record_oak_stream.py"
mkdir -p "${HOME}/.config/systemd/user/ecs-record-oak-mcap.service.d"
mkdir -p "${HOME}/.config/systemd/user/ecs-ego-web.service.d"
install -m 0644 /tmp/z-mcap-production.conf "${HOME}/.config/systemd/user/ecs-record-oak-mcap.service.d/z-mcap-production.conf"
install -m 0644 /tmp/z-ego-web-mcap-production.conf "${HOME}/.config/systemd/user/ecs-ego-web.service.d/z-mcap-production.conf"
install -m 0644 /tmp/ego_web.py "${REMOTE_WEB}/ego_web.py"

/home/server/workspace/ego-studio/.venv/bin/python -m py_compile \
  "${REMOTE_CAPTURE}/record_oak_stream.py" \
  "${REMOTE_CAPTURE}/oak_warm_session.py" \
  "${REMOTE_CAPTURE}/capture_stop_state.py" \
  "${REMOTE_CAPTURE}/async_frame_writer.py" \
  "${REMOTE_CLI}/record_oak_stream.py" \
  "${REMOTE_WEB}/ego_web.py"
PYTHONPATH="/home/server/workspace/ego-studio/src" /home/server/workspace/ego-studio/.venv/bin/python -c \
  "from ego_capture_studio.capture.record_oak_stream import main, _append_capture_frame; from ego_capture_studio.cli.record_oak_stream import _load_capture_main; assert callable(main); assert callable(_load_capture_main()); print('remote_cli_import_ok')"

systemctl --user daemon-reload
systemctl --user restart ecs-ego-web.service
if systemctl --user is-enabled ecs-ego-tcp-bridge.service >/dev/null 2>&1; then
  systemctl --user restart ecs-ego-tcp-bridge.service
fi
echo "deploy_ok capture_py=${REMOTE_CAPTURE}/record_oak_stream.py"
grep -E 'EGO_OAK_WARM_(IDLE|COLD_EPISODE)' "${HOME}/.config/systemd/user/ecs-record-oak-mcap.service.d/z-mcap-production.conf"
grep -E 'EGO_OAK_WARM_(IDLE|COLD_EPISODE)' "${HOME}/.config/systemd/user/ecs-ego-web.service.d/z-mcap-production.conf"
REMOTE
)"

bash "$(cd "$(dirname "$0")" && pwd)/ego-130-post-deploy-smoke.sh" "${TARGET}"

echo "==> Done. Capture process picks up new code on next start (after stop)."
