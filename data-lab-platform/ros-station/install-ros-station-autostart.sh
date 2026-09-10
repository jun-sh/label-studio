#!/usr/bin/env bash
# Install ROS station systemd autostart + daily reboot cron.
# Run on the target host (10.10.10.200):
#   sudo bash install-ros-station-autostart.sh
# Optional env:
#   REBOOT_HOUR=4 REBOOT_MINUTE=0 ROS_STATION_USER=server
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/ros-station"
SERVICE_NAME="ros-station.service"
REBOOT_HOUR="${REBOOT_HOUR:-4}"
REBOOT_MINUTE="${REBOOT_MINUTE:-0}"
ROS_STATION_USER="${ROS_STATION_USER:-server}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo: sudo bash $0" >&2
  exit 1
fi

if ! id "${ROS_STATION_USER}" &>/dev/null; then
  echo "User ${ROS_STATION_USER} not found; set ROS_STATION_USER=..." >&2
  exit 1
fi

# Camera devices are group-owned by 'video'; ensure the service user can access them at boot.
if getent group video &>/dev/null; then
  usermod -aG video "${ROS_STATION_USER}" 2>/dev/null || true
fi

install -d -m 755 "${INSTALL_DIR}"
install -m 755 "${SCRIPT_DIR}/ros-station-start.sh" "${INSTALL_DIR}/ros-station-start.sh"

# Patch User/Group and workspace path in the unit file.
tmp_unit="$(mktemp)"
sed \
  -e "s|^User=.*|User=${ROS_STATION_USER}|" \
  -e "s|^Group=.*|Group=${ROS_STATION_USER}|" \
  -e "s|^SupplementaryGroups=.*|SupplementaryGroups=video|" \
  -e "s|^Environment=HOME=.*|Environment=HOME=/home/${ROS_STATION_USER}|" \
  -e "s|^Environment=DISPLAY=.*|Environment=DISPLAY=:0|" \
  -e "s|^Environment=XAUTHORITY=.*|Environment=XAUTHORITY=/home/${ROS_STATION_USER}/.Xauthority|" \
  -e "s|^WorkingDirectory=.*|WorkingDirectory=/home/${ROS_STATION_USER}|" \
  -e "s|^Environment=ROS_STATION_WS=.*|Environment=ROS_STATION_WS=/home/${ROS_STATION_USER}/catkin_ws/devel/setup.bash|" \
  "${SCRIPT_DIR}/ros-station.service" > "${tmp_unit}"
install -m 644 "${tmp_unit}" "/etc/systemd/system/${SERVICE_NAME}"
rm -f "${tmp_unit}"

# Stop manually started ROS stack to avoid port conflicts on first enable.
log_stop() { echo "[ros-station-install] $*"; }
log_stop "stopping any existing roscore/rosbridge processes..."
pkill -f "roscore|rosmaster|multi_cam_publisher|rosbridge_websocket|relay_bridge.launch" 2>/dev/null || true
sleep 3

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}" || systemctl start "${SERVICE_NAME}"

# Daily reboot (default 04:00) to avoid long-run stalls.
CRON_FILE="/etc/cron.d/ros-station-daily-reboot"
cat > "${CRON_FILE}" <<EOF
# ROS station daily reboot — installed by install-ros-station-autostart.sh
${REBOOT_MINUTE} ${REBOOT_HOUR} * * * root /sbin/shutdown -r now
EOF
chmod 644 "${CRON_FILE}"

configure_gdm_autologin() {
  if [[ ! -f /etc/gdm3/custom.conf ]]; then
    echo "WARN: /etc/gdm3/custom.conf not found; skip autologin setup" >&2
    return 0
  fi
  local tmp_gdm
  tmp_gdm="$(mktemp)"
  cat > "${tmp_gdm}" <<EOF
# GDM configuration storage
[daemon]
AutomaticLoginEnable=true
AutomaticLogin=${ROS_STATION_USER}

[security]

[xdmcp]

[chooser]

[debug]
EOF
  cp "${tmp_gdm}" /etc/gdm3/custom.conf
  rm -f "${tmp_gdm}"
  echo "GDM autologin enabled for ${ROS_STATION_USER}"
}

configure_gdm_autologin

echo ""
echo "=== ROS station autostart installed ==="
echo "  scripts:  ${INSTALL_DIR}/"
echo "  service:  ${SERVICE_NAME} (enabled, started)"
echo "  reboot:   daily at ${REBOOT_HOUR}:$(printf '%02d' "${REBOOT_MINUTE}") via ${CRON_FILE}"
echo ""
systemctl --no-pager status "${SERVICE_NAME}" || true
echo ""
echo "Logs: journalctl -u ${SERVICE_NAME} -f"
