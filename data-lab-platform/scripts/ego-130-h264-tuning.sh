#!/usr/bin/env bash
# Optional 130 H264 remux tuning (genpts). Does not change SEGMENT_H264=1 baseline.
# Usage: bash data-lab-platform/scripts/ego-130-h264-tuning.sh [user@host] {enable|disable|status}
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
ACTION="${2:-status}"
DROPIN_NAME="v0.0.8-segment-mp4-genpts.conf"
REMOTE_DIR="/home/server/.config/systemd/user/ecs-record-oak-stream.service.d"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="${SCRIPT_DIR}/../ego-stream-client/systemd/ecs-record-oak-stream.service.d/v0.0.8-segment-mp4-genpts.conf.disabled"

ssh_cmd() {
  ssh -o StrictHostKeyChecking=no "${TARGET}" "$@"
}

case "${ACTION}" in
  enable)
    ssh_cmd "mkdir -p ${REMOTE_DIR}"
    scp -q "${SRC}" "${TARGET}:${REMOTE_DIR}/${DROPIN_NAME}"
    ssh_cmd "systemctl --user daemon-reload && systemctl --user restart ecs-record-oak-stream"
    echo "enabled ${DROPIN_NAME} on ${TARGET}"
    ;;
  disable)
    ssh_cmd "rm -f ${REMOTE_DIR}/${DROPIN_NAME} && systemctl --user daemon-reload && systemctl --user restart ecs-record-oak-stream"
    echo "disabled ${DROPIN_NAME} on ${TARGET}"
    ;;
  status)
    ssh_cmd "ls -la ${REMOTE_DIR}/${DROPIN_NAME} 2>/dev/null || echo 'genpts drop-in: not installed'"
    ssh_cmd "systemctl --user show ecs-record-oak-stream -p Environment --no-pager | tr ' ' '\\n' | grep -E 'SEGMENT_H264|FFMPEG_INPUT' || true"
    ;;
  *)
    echo "usage: $0 [user@host] {enable|disable|status}" >&2
    exit 2
    ;;
esac
