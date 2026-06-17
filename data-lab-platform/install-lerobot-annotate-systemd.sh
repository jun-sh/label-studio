#!/usr/bin/env bash
# Install LeRobot Annotate systemd unit for boot auto-start.
#   sudo bash data-lab-platform/install-lerobot-annotate-systemd.sh        # system unit (recommended)
#   bash data-lab-platform/install-lerobot-annotate-systemd.sh --user      # user unit (needs linger for boot)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_NAME="data-lab-lerobot-annotate.service"
MODE="${1:-system}"

chmod +x "$SCRIPT_DIR/lerobot-annotate/start-lerobot-annotate.sh"
chmod +x "$SCRIPT_DIR/lerobot-annotate/stop-lerobot-annotate.sh"

if [[ "$MODE" == "--user" ]]; then
  SRC="$SCRIPT_DIR/systemd/data-lab-lerobot-annotate.user.service"
  DEST_DIR="$HOME/.config/systemd/user"
  DEST="$DEST_DIR/$UNIT_NAME"
  mkdir -p "$DEST_DIR"
  install -m 644 "$SRC" "$DEST"
  systemctl --user daemon-reload
  systemctl --user enable "$UNIT_NAME"
  systemctl --user restart "$UNIT_NAME"
  systemctl --user --no-pager status "$UNIT_NAME" || true
  echo ""
  echo "User unit enabled. For boot without login, run once:"
  echo "  sudo loginctl enable-linger $(whoami)"
  echo "Logs: journalctl --user -u $UNIT_NAME -f"
  exit 0
fi

if [[ "$MODE" != "system" ]]; then
  echo "Usage: sudo bash $0 | bash $0 --user" >&2
  exit 1
fi

SRC="$SCRIPT_DIR/systemd/$UNIT_NAME"
DEST="/etc/systemd/system/$UNIT_NAME"

if [[ ! -f "$SRC" ]]; then
  echo "Missing unit file: $SRC" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo: sudo bash $0" >&2
  exit 1
fi

install -m 644 "$SRC" "$DEST"
systemctl daemon-reload
systemctl enable "$UNIT_NAME"
systemctl restart "$UNIT_NAME"
systemctl --no-pager status "$UNIT_NAME" || true
echo ""
echo "Enabled: systemctl is-enabled $UNIT_NAME"
echo "Logs:    journalctl -u $UNIT_NAME -f"
echo "Health:  curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7861/"
