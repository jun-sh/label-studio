#!/usr/bin/env bash
# Optional 130 H264 remux tuning (genpts). Does not change SEGMENT_H264=1 baseline.
# Usage: bash data-lab-platform/scripts/ego-130-h264-tuning.sh [user@host] {enable|disable|status}
# Non-interactive: set EGO_SSH_PASSWORD=1 (or use ssh keys).
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
ACTION="${2:-status}"
DROPIN_NAME="v0.0.8-segment-mp4-genpts.conf"
REMOTE_DIR="/home/server/.config/systemd/user/ecs-record-oak-stream.service.d"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="${SCRIPT_DIR}/../ego-stream-client/systemd/ecs-record-oak-stream.service.d/v0.0.8-segment-mp4-genpts.conf.disabled"
PASS="${EGO_SSH_PASSWORD:-}"

run_remote() {
  local cmd="$1"
  if [[ -n "${PASS}" ]]; then
    EGO_REMOTE_CMD="${cmd}" EGO_REMOTE_TARGET="${TARGET}" python3 - <<'PY'
import os, paramiko
target = os.environ["EGO_REMOTE_TARGET"]
cmd = os.environ["EGO_REMOTE_CMD"]
password = os.environ.get("EGO_SSH_PASSWORD", "")
user, host = target.split("@", 1) if "@" in target else ("server", target)
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=password, timeout=15)
_, o, e = c.exec_command(cmd, timeout=120)
print(o.read().decode(), end="")
err = e.read().decode().strip()
if err:
    print(err, file=__import__("sys").stderr)
c.close()
PY
  else
    ssh -o StrictHostKeyChecking=no "${TARGET}" "${cmd}"
  fi
}

copy_dropin() {
  if [[ -n "${PASS}" ]]; then
    python3 - <<PY
import os, paramiko
user, host = "${TARGET}".split("@", 1) if "@" in "${TARGET}" else ("server", "${TARGET}")
password = os.environ.get("EGO_SSH_PASSWORD","")
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy()); c.connect(host, username=user, password=password, timeout=15)
sftp=c.open_sftp(); sftp.put("${SRC}", f"${REMOTE_DIR}/${DROPIN_NAME}"); sftp.close(); c.close()
PY
  else
    scp -q "${SRC}" "${TARGET}:${REMOTE_DIR}/${DROPIN_NAME}"
  fi
}

case "${ACTION}" in
  enable)
    run_remote "mkdir -p ${REMOTE_DIR}"
    copy_dropin
    run_remote "systemctl --user daemon-reload && systemctl --user restart ecs-record-oak-stream"
    echo "enabled ${DROPIN_NAME} on ${TARGET}"
    ;;
  disable)
    run_remote "rm -f ${REMOTE_DIR}/${DROPIN_NAME} && systemctl --user daemon-reload && systemctl --user restart ecs-record-oak-stream"
    echo "disabled ${DROPIN_NAME} on ${TARGET}"
    ;;
  status)
    run_remote "ls -la ${REMOTE_DIR}/${DROPIN_NAME} 2>/dev/null || echo 'genpts drop-in: not installed'"
    run_remote "systemctl --user show ecs-record-oak-stream -p Environment --no-pager | tr ' ' '\\n' | grep -E 'SEGMENT_H264|FFMPEG_INPUT' || true"
    ;;
  *)
    echo "usage: $0 [user@host] {enable|disable|status}" >&2
    exit 2
    ;;
esac
