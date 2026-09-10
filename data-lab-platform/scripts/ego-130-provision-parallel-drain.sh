#!/usr/bin/env bash
# Provision ego-parallel-drain-poc on 130 (isolated from ego-001 production).
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
STATION_ID="ego-parallel-drain-poc"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CAPTURE_SRC="${ROOT}/ego-stream-client"
REMOTE_STUDIO="/home/server/workspace/ego-studio"
REMOTE_CAPTURE="${REMOTE_STUDIO}/src/ego_capture_studio/capture"
REMOTE_CLI="${REMOTE_STUDIO}/src/ego_capture_studio/cli"
REMOTE_SYSTEMD="/home/server/.config/systemd/user"
CACHE_ROOT="/home/server/cache/${STATION_ID}"

_ssh() {
  if ssh -o BatchMode=yes -o ConnectTimeout=8 "${TARGET}" "$@"; then
    return 0
  fi
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" TARGET="$TARGET" REMOTE_CMD="$*" python3 - <<'PY'
import os, paramiko, shlex
target = os.environ["TARGET"]
user, _, host = target.partition("@")
cmd = os.environ["REMOTE_CMD"]
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=20)
_, o, e = c.exec_command(cmd, timeout=300)
out = (o.read() + e.read()).decode()
print(out, end="")
raise SystemExit(o.channel.recv_exit_status())
PY
}

_rsync() {
  if rsync -av "$@" 2>/dev/null; then
    return 0
  fi
  echo "rsync failed, retrying with scp for critical files" >&2
  return 1
}

echo "==> Provision parallel-drain POC ${TARGET} station=${STATION_ID}"

_ssh "mkdir -p ${REMOTE_CAPTURE} ${REMOTE_CLI} ${CACHE_ROOT}/segments /tmp/ego-parallel-drain-poc-active"

echo "==> Sync capture code"
_rsync --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "${CAPTURE_SRC}/" \
  "${TARGET}:${REMOTE_CAPTURE}/" || true
_rsync "${CAPTURE_SRC}/cli/record_oak_stream.py" "${TARGET}:${REMOTE_CLI}/record_oak_stream.py" || \
  scp -q "${CAPTURE_SRC}/cli/record_oak_stream.py" "${TARGET}:${REMOTE_CLI}/record_oak_stream.py"
_rsync "${CAPTURE_SRC}/cli/record_oak_stream_parallel.py" "${TARGET}:${REMOTE_CLI}/record_oak_stream_parallel.py" || \
  scp -q "${CAPTURE_SRC}/cli/record_oak_stream_parallel.py" "${TARGET}:${REMOTE_CLI}/record_oak_stream_parallel.py"

echo "==> Install parallel-drain systemd unit"
_ssh "mkdir -p ${REMOTE_SYSTEMD}/ecs-record-oak-mcap-parallel-drain.service.d"
_rsync "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap-parallel-drain.service" \
  "${TARGET}:${REMOTE_SYSTEMD}/ecs-record-oak-mcap-parallel-drain.service" || \
  scp -q "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap-parallel-drain.service" \
    "${TARGET}:${REMOTE_SYSTEMD}/ecs-record-oak-mcap-parallel-drain.service"
_rsync "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap-parallel-drain.service.d/z-parallel-drain.conf" \
  "${TARGET}:${REMOTE_SYSTEMD}/ecs-record-oak-mcap-parallel-drain.service.d/z-parallel-drain.conf" || \
  scp -q "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap-parallel-drain.service.d/z-parallel-drain.conf" \
    "${TARGET}:${REMOTE_SYSTEMD}/ecs-record-oak-mcap-parallel-drain.service.d/z-parallel-drain.conf"

_ssh "systemctl --user daemon-reload"

echo "==> Parallel-drain unit installed (not started)."
echo "    Record: bash data-lab-platform/scripts/ego-130-record-parallel-drain-poc.sh ${TARGET}"
