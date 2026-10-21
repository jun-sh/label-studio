#!/usr/bin/env bash
# Install SAM ML backend systemd unit (does NOT enable boot or start by default).
#
#   sudo bash ml-services/install-sam-systemd.sh
#   sudo bash ml-services/install-sam-systemd.sh --enable-boot
#   sudo bash ml-services/install-sam-systemd.sh --enable-boot --start
#   bash ml-services/install-sam-systemd.sh --user
#   bash ml-services/install-sam-systemd.sh --user --enable-boot --start
#
# Day-2 ops: bash ml-services/sam-backendctl.sh {start|stop|status|enable-boot|disable-boot}
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_NAME="data-lab-sam-backend.service"
MODE="system"
ENABLE_BOOT=0
DO_START=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) MODE="user"; shift ;;
    --enable-boot) ENABLE_BOOT=1; shift ;;
    --start) DO_START=1; shift ;;
    system) MODE="system"; shift ;;
    -h|--help)
      sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "$MODE" == "user" ]]; then
  SRC="$SCRIPT_DIR/systemd/data-lab-sam-backend.user.service"
  DEST_DIR="$HOME/.config/systemd/user"
  DEST="$DEST_DIR/$UNIT_NAME"
  mkdir -p "$DEST_DIR"
  install -m 644 "$SRC" "$DEST"
  systemctl --user daemon-reload
  if [[ "$ENABLE_BOOT" -eq 1 ]]; then
    systemctl --user enable "$UNIT_NAME"
    echo "User unit enabled for boot."
  else
    systemctl --user disable "$UNIT_NAME" 2>/dev/null || true
    echo "User unit installed; boot autostart OFF (default)."
  fi
  if [[ "$DO_START" -eq 1 ]]; then
    systemctl --user start "$UNIT_NAME"
  fi
  systemctl --user --no-pager status "$UNIT_NAME" 2>/dev/null || true
  echo ""
  echo "Start GPU backend when needed:"
  echo "  bash ml-services/sam-backendctl.sh start"
  echo "Enable boot (optional):"
  echo "  bash ml-services/sam-backendctl.sh enable-boot"
  echo "  sudo loginctl enable-linger $(whoami)   # boot without login"
  exit 0
fi

SRC="$SCRIPT_DIR/systemd/$UNIT_NAME"
DEST="/etc/systemd/system/$UNIT_NAME"

if [[ ! -f "$SRC" ]]; then
  echo "Missing unit file: $SRC" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo: sudo bash $0 [--enable-boot] [--start]" >&2
  exit 1
fi

install -m 644 "$SRC" "$DEST"
systemctl daemon-reload

if [[ "$ENABLE_BOOT" -eq 1 ]]; then
  systemctl enable "$UNIT_NAME"
  echo "System unit enabled for boot."
else
  systemctl disable "$UNIT_NAME" 2>/dev/null || true
  echo "System unit installed; boot autostart OFF (default)."
fi

if [[ "$DO_START" -eq 1 ]]; then
  systemctl start "$UNIT_NAME"
fi

systemctl --no-pager status "$UNIT_NAME" 2>/dev/null || true
echo ""
echo "Policy: SAM does not occupy GPU until you start it."
echo "  bash ml-services/sam-backendctl.sh start"
echo "  bash ml-services/sam-backendctl.sh disable-boot   # stop + no reboot autostart"
echo "Logs: journalctl -u $UNIT_NAME -f"
