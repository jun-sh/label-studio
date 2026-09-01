#!/usr/bin/env bash
# Provision 130 edge for ego-001 MCAP production (VPU H.264).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SYSTEMD_SRC="${ROOT}/ego-stream-client/systemd"

echo "[provision-mcap] installing ecs-record-oak-mcap.service + drop-in"
mkdir -p "${HOME}/.config/systemd/user/ecs-record-oak-mcap.service.d"
cp "${SYSTEMD_SRC}/ecs-record-oak-mcap.service" "${HOME}/.config/systemd/user/"
cp "${SYSTEMD_SRC}/ecs-record-oak-mcap.service.d/z-mcap-production.conf" \
  "${HOME}/.config/systemd/user/ecs-record-oak-mcap.service.d/"

echo "[provision-mcap] disabling legacy tar.zst recorder"
systemctl --user disable --now ecs-record-oak-stream.service 2>/dev/null || true
systemctl --user disable --now ecs-record-oak-mcap-pilot.service 2>/dev/null || true
systemctl --user disable --now ecs-record-oak-mcap-track2.service 2>/dev/null || true

systemctl --user daemon-reload
systemctl --user enable ecs-record-oak-mcap.service

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
install -m 0755 "${SCRIPT_DIR}/ego-upload-station.sh" "${HOME}/.local/bin/ego-upload"

echo "[provision-mcap] done. Start: systemctl --user start ecs-record-oak-mcap.service"
