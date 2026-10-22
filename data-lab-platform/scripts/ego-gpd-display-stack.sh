#!/usr/bin/env bash
# GPD MicroPC 2 display stack — ego-001 §2 parity (GRUB, logind, acpi/udev/systemd, iio mask).
# Also §2.10 capture power: GNOME never auto-suspend + performance power profile (lid-closed recording).
# SSOT: docs/gpd-micropc2-capture-station-setup.md §2 + §2.10
#
# On edge host:  sudo bash ego-gpd-display-stack.sh
# From dev:      RC_CAPTURE_PASS=1 bash ego-gpd-display-stack.sh server@10.10.11.52
set -euo pipefail

GRUB_LINE='GRUB_CMDLINE_LINUX_DEFAULT="quiet splash fbcon=rotate:1 video=DSI-1:panel_orientation=right_side_up"'

_run_local() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "Run as root on the GPD host: sudo bash $0" >&2
    exit 1
  fi

  install -d /etc/X11/xorg.conf.d /etc/systemd/logind.conf.d /etc/acpi/events
  install -d /etc/systemd/system-sleep /etc/X11/Xsession.d /etc/udev/rules.d

  # §2.2 GRUB
  if grep -q 'panel_orientation=right_side_up' /etc/default/grub 2>/dev/null; then
    echo "GRUB: panel_orientation already set"
  else
    sed -i "s|^GRUB_CMDLINE_LINUX_DEFAULT=.*|${GRUB_LINE}|" /etc/default/grub
    update-grub
    echo "GRUB: updated (reboot required for kernel cmdline)"
  fi

  # §2.3 Xorg
  cat > /etc/X11/xorg.conf.d/20-monitor.conf <<'EOF'
Section "Monitor"
    Identifier "DSI-1"
    Option "Rotate" "right"
EndSection
EOF

  # §2.4 fix script
  cat > /usr/local/bin/gpd-fix-display-rotate.sh <<'EOF'
#!/bin/bash
# GPD MicroPC 2: 1280x800 landscape (native 1080x1920 + rotate right + scale).
set -euo pipefail
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/home/server/.Xauthority}"
OUT="DSI-1"
MODE="1080x1920"
ROTATE="right"
SCALE="0.6666666666666666x0.7407407407407407"
TARGET_W=1280
TARGET_H=800

if ! xrandr --query 2>/dev/null | grep -q "^${OUT} connected"; then
  exit 0
fi

line=$(xrandr --query | awk -v o="$OUT" '$1==o {print; exit}')
rot="unknown"
if [[ "$line" =~ \+[0-9]+\+[0-9]+\ \(0x[0-9a-fA-F]+\)\ ([a-z]+) ]]; then
  rot="${BASH_REMATCH[1]}"
elif [[ "$line" =~ \+[0-9]+\+[0-9]+\ \(([a-z]+) ]]; then
  rot="${BASH_REMATCH[1]}"
fi

read -r cur_w cur_h _ < <(xdpyinfo 2>/dev/null | awk '/dimensions:/ {gsub(/x/, " "); print $2, $3}')

if [[ "${cur_w:-0}" == "$TARGET_W" && "${cur_h:-0}" == "$TARGET_H" && "$rot" == "$ROTATE" ]]; then
  exit 0
fi

xrandr --output "$OUT" --mode "$MODE" --rotate "$ROTATE" --scale "$SCALE"
logger -t gpd-fix-display "set ${OUT} to ${TARGET_W}x${TARGET_H} ${ROTATE} (was ${cur_w:-?}x${cur_h:-?} ${rot})"
EOF
  chmod 0755 /usr/local/bin/gpd-fix-display-rotate.sh
  ln -sf /usr/local/bin/gpd-fix-display-rotate.sh /usr/local/bin/gpd-display-fix.sh

  # §2.5 iio-sensor-proxy (GNOME lock as server)
  systemctl stop iio-sensor-proxy 2>/dev/null || true
  systemctl mask iio-sensor-proxy 2>/dev/null || true
  if id server &>/dev/null && [[ -S /run/user/1000/bus ]]; then
    sudo -u server DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
      gsettings set org.gnome.settings-daemon.peripherals.touchscreen orientation-lock true 2>/dev/null || true
  fi

  # §2.6 logind (match ego-001 field capture)
  cat > /etc/systemd/logind.conf.d/ego-capture.conf <<'EOF'
[Login]
# EGO field capture: lid closed must not suspend/sleep
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
EOF
  systemctl restart systemd-logind || true

  # §2.10 GNOME/dconf: logind ignore alone is NOT enough (battery auto-suspend, balanced CPU).
  install -d /etc/dconf/db/local.d /etc/dconf/db/local.d/locks
  cat > /etc/dconf/db/local.d/01-ego-capture-power <<'EOF'
# EGO edge capture: screen may blank; system must not suspend or throttle for idle/lid.
[org/gnome/settings-daemon/plugins/power]
sleep-inactive-ac-type='nothing'
sleep-inactive-battery-type='nothing'
sleep-inactive-ac-timeout=0
sleep-inactive-battery-timeout=0
idle-dim=false

[org/gnome/desktop/session]
idle-delay=uint32 0
EOF
  cat > /etc/dconf/db/local.d/locks/01-ego-capture-power <<'EOF'
/org/gnome/settings-daemon/plugins/power/sleep-inactive-ac-type
/org/gnome/settings-daemon/plugins/power/sleep-inactive-battery-type
/org/gnome/settings-daemon/plugins/power/sleep-inactive-ac-timeout
/org/gnome/settings-daemon/plugins/power/sleep-inactive-battery-timeout
EOF
  if [[ -f /etc/dconf/profile/user ]]; then
    if ! grep -q '^system-db:local' /etc/dconf/profile/user 2>/dev/null; then
      printf '%s\n' 'system-db:local' >> /etc/dconf/profile/user
    fi
  else
    install -d /etc/dconf/profile
    printf '%s\n' 'user-db:user' 'system-db:local' > /etc/dconf/profile/user
  fi
  if command -v dconf >/dev/null 2>&1; then
    dconf update
  fi

  _apply_server_gsettings_power() {
    local bus="/run/user/1000/bus"
    [[ -S "$bus" ]] || return 0
    id server &>/dev/null || return 0
    local run_gs=(gsettings set)
    sudo -u server DBUS_SESSION_BUS_ADDRESS="unix:path=${bus}" \
      "${run_gs[@]}" org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing 2>/dev/null || true
    sudo -u server DBUS_SESSION_BUS_ADDRESS="unix:path=${bus}" \
      "${run_gs[@]}" org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type nothing 2>/dev/null || true
    sudo -u server DBUS_SESSION_BUS_ADDRESS="unix:path=${bus}" \
      "${run_gs[@]}" org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout 0 2>/dev/null || true
    sudo -u server DBUS_SESSION_BUS_ADDRESS="unix:path=${bus}" \
      "${run_gs[@]}" org.gnome.settings-daemon.plugins.power sleep-inactive-battery-timeout 0 2>/dev/null || true
    sudo -u server DBUS_SESSION_BUS_ADDRESS="unix:path=${bus}" \
      "${run_gs[@]}" org.gnome.settings-daemon.plugins.power idle-dim false 2>/dev/null || true
    sudo -u server DBUS_SESSION_BUS_ADDRESS="unix:path=${bus}" \
      "${run_gs[@]}" org.gnome.desktop.session idle-delay 0 2>/dev/null || true
  }
  _apply_server_gsettings_power

  cat > /etc/systemd/system/ego-capture-power-profile.service <<'EOF'
[Unit]
Description=EGO capture station: performance power profile (no idle power-saver)
After=power-profiles-daemon.service
Wants=power-profiles-daemon.service

[Service]
Type=oneshot
ExecStart=/usr/bin/powerprofilesctl set performance
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable ego-capture-power-profile.service 2>/dev/null || true
  if command -v powerprofilesctl >/dev/null 2>&1; then
    powerprofilesctl set performance 2>/dev/null || true
  fi

  # §2.7 acpi / sleep / xsession / udev / systemd
  cat > /etc/acpi/gpd-lid-fix.sh <<'EOF'
#!/bin/bash
sleep 1
su - server -c 'DISPLAY=:0 XAUTHORITY=/home/server/.Xauthority /usr/local/bin/gpd-fix-display-rotate.sh'
EOF
  chmod 0755 /etc/acpi/gpd-lid-fix.sh

  cat > /etc/acpi/events/lid-fix <<'EOF'
event=button/lid.*
action=/etc/acpi/gpd-lid-fix.sh
EOF

  cat > /etc/systemd/system-sleep/99-gpd-fix-display-rotate.sh <<'EOF'
#!/bin/bash
case "$1" in
  post)
    sleep 2
    su - server -c 'DISPLAY=:0 XAUTHORITY=/home/server/.Xauthority /usr/local/bin/gpd-fix-display-rotate.sh'
    ;;
esac
EOF
  chmod 0755 /etc/systemd/system-sleep/99-gpd-fix-display-rotate.sh

  cat > /etc/X11/Xsession.d/99-gpd-fix-display-rotate <<'EOF'
#!/bin/sh
/usr/local/bin/gpd-fix-display-rotate.sh >/dev/null 2>&1 || true
EOF
  chmod 0755 /etc/X11/Xsession.d/99-gpd-fix-display-rotate

  cat > /etc/udev/rules.d/99-gpd-display-rotate.rules <<'EOF'
ACTION=="change", SUBSYSTEM=="acpi", KERNEL=="PNP0C0D:00", RUN+="/bin/systemctl start gpd-fix-display-rotate.service"
ACTION=="change", SUBSYSTEM=="drm", KERNEL=="card0-DSI-1", RUN+="/bin/systemctl start gpd-fix-display-rotate.service"
EOF

  cat > /etc/systemd/system/gpd-fix-display-rotate.service <<'EOF'
[Unit]
Description=Fix GPD MicroPC 2 display rotation
After=graphical.target

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 1
ExecStart=/bin/su - server -c 'DISPLAY=:0 XAUTHORITY=/home/server/.Xauthority /usr/local/bin/gpd-fix-display-rotate.sh'

[Install]
WantedBy=multi-user.target
EOF

  if ! command -v acpid >/dev/null 2>&1; then
    apt-get install -y acpid
  fi

  systemctl daemon-reload
  systemctl enable gpd-fix-display-rotate.service
  systemctl enable --now acpid 2>/dev/null || true
  systemctl restart acpid 2>/dev/null || true
  udevadm control --reload-rules

  echo "OK: GPD display stack installed (ego-001 §2 + §2.10 capture power)"
  echo "Verify: systemctl is-enabled iio-sensor-proxy → masked"
  echo "        test -f /etc/systemd/logind.conf.d/ego-capture.conf"
  echo "        test -f /etc/dconf/db/local.d/01-ego-capture-power"
  echo "        powerprofilesctl get → performance"
  echo "        grep panel_orientation /etc/default/grub"
  echo "Reboot once so GRUB cmdline takes effect."
}

_ssh() {
  if [[ -n "${RC_CAPTURE_PASS:-}" ]] && command -v sshpass >/dev/null 2>&1; then
    sshpass -p "${RC_CAPTURE_PASS}" ssh -o StrictHostKeyChecking=accept-new "$@"
  else
    ssh -o StrictHostKeyChecking=accept-new "$@"
  fi
}

_scp() {
  if [[ -n "${RC_CAPTURE_PASS:-}" ]] && command -v sshpass >/dev/null 2>&1; then
    sshpass -p "${RC_CAPTURE_PASS}" scp -o StrictHostKeyChecking=accept-new "$@"
  else
    scp -o StrictHostKeyChecking=accept-new "$@"
  fi
}

if [[ $# -ge 1 ]]; then
  TARGET="$1"
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  SUDO_PW="${RC_CAPTURE_PASS:-1}"
  _scp "${SCRIPT_DIR}/$(basename "$0")" "${TARGET}:/tmp/ego-gpd-display-stack.sh"
  _ssh "${TARGET}" "echo ${SUDO_PW} | sudo -S -p '' bash /tmp/ego-gpd-display-stack.sh"
else
  _run_local
fi
