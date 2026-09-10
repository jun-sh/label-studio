#!/usr/bin/env bash
# One-shot bootstrap for 10.10.10.200: add SSH key + install ROS station autostart.
# Run on 10.10.10.200:
#   curl -fsSL http://10.10.10.34:8877/bootstrap-ros-station-200.sh | sudo -E bash
set -euo pipefail

BASE_URL="${ROS_STATION_BASE_URL:-http://10.10.10.34:8877}"
WORKDIR="/tmp/ros-station-provision-$$"
REMOTE_USER="${SUDO_USER:-${USER:-server}}"
REBOOT_HOUR="${REBOOT_HOUR:-4}"
REBOOT_MINUTE="${REBOOT_MINUTE:-0}"
DGX_PUBKEY="ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCd4W+moZE8OogE7ke/EPdgwWWH0SBd4JEXqSVSWqlcqNvgOZhp46J4ngHSUDG2PW+Tmoj+89az6uMXjATvzFTzbw61I4URrjyzKxhzJkTTO7+oa7qmb813pOy1ogoZUBtue84lkGHM27+MSjJ8QLZou1M4P3AmEN7QuLYbx1i/+sJkWJcROGaJqLosGA6BZiSup5PVGTeNsWoQPNJyg7Zrj+nRGHPnHJ3gqrswjoXcfZvtqqPy7qelxyDJ2fIdPXIbUeuoYCRJWlGb7ocRYFGp4pfQOMN+l0PhjdB+bD7T/lEoM+dDuPUvZ06sMtHbhT0q1DxDb3QtAvKu0F8T45Hupsc5lRiTfHEHIrC2CK2dUHSHhh9S8CiwbA5+PrdnkfbRwuUMkF5kN30n7K0RQSwfVDPG6Mvo6WKpPuMbcONyDWB/pCzZqDFXk3rmdi6j5aERzNlmSUUq4pdTlm6sBzDGReQmqnLCFXHvFBsDQsmlfgFAPHbWUScm5dww30RaAV0= dgx@dgx.com"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo: curl -fsSL ${BASE_URL}/bootstrap-ros-station-200.sh | sudo -E bash" >&2
  exit 1
fi

echo "[bootstrap] user=${REMOTE_USER} base=${BASE_URL}"
install -d -m 700 "/home/${REMOTE_USER}/.ssh"
auth="/home/${REMOTE_USER}/.ssh/authorized_keys"
touch "${auth}"
grep -qF "${DGX_PUBKEY}" "${auth}" 2>/dev/null || echo "${DGX_PUBKEY}" >> "${auth}"
chown -R "${REMOTE_USER}:${REMOTE_USER}" "/home/${REMOTE_USER}/.ssh"
chmod 600 "${auth}"

mkdir -p "${WORKDIR}"
cd "${WORKDIR}"
for f in install-ros-station-autostart.sh ros-station-start.sh ros-station.service; do
  curl -fsSL "${BASE_URL}/${f}" -o "${f}"
done
chmod +x install-ros-station-autostart.sh ros-station-start.sh
env REBOOT_HOUR="${REBOOT_HOUR}" REBOOT_MINUTE="${REBOOT_MINUTE}" ROS_STATION_USER="${REMOTE_USER}" \
  bash install-ros-station-autostart.sh

echo "[bootstrap] done — ssh key added for DGX; ros-station enabled"
