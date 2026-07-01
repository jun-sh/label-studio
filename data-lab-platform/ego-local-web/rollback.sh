#!/usr/bin/env bash
# Remove EGO local web (user-level only; polkit/hotspot need sudo rollback — see README).
set -euo pipefail

DEST="${EGO_WEB_DEST:-/home/server/ego-web}"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

systemctl --user disable --now ecs-ego-web.service 2>/dev/null || true
rm -f "$SYSTEMD_USER_DIR/ecs-ego-web.service"
systemctl --user daemon-reload

rm -rf "$DEST"

echo "============================================"
echo "EGO local web removed (user units + $DEST)"
echo "To remove polkit/hotspot (sudo once):"
echo "  sudo rm -f /etc/polkit-1/rules.d/50-ego-hotspot.rules"
echo "  sudo systemctl disable --now ecs-ego-hotspot.service"
echo "  sudo rm -f /etc/systemd/system/ecs-ego-hotspot.service"
echo "  sudo rm -f /etc/sudoers.d/ego-hotspot"
echo "  nmcli connection delete EGO-214-COLLECT 2>/dev/null || true"
echo "============================================"
