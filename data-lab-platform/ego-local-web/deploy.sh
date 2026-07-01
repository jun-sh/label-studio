#!/usr/bin/env bash
# Deploy EGO local web control to 214 (run on edge host as user server).
# Polkit + hotspot steps need one-time sudo — see README.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${EGO_WEB_DEST:-/home/server/ego-web}"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$DEST" "$SYSTEMD_USER_DIR"

install -m 0644 "$SCRIPT_DIR/ego_web.py" "$DEST/ego_web.py"
install -m 0644 "$SCRIPT_DIR/README.md" "$DEST/README.md"
if [[ -f /home/server/ego-web/ego-hotspot-up.sh ]]; then
  :
elif [[ -f "$SCRIPT_DIR/scripts/ego-hotspot-up.sh" ]]; then
  install -m 0755 "$SCRIPT_DIR/scripts/ego-hotspot-up.sh" "$DEST/ego-hotspot-up.sh"
  install -m 0755 "$SCRIPT_DIR/scripts/ego-hotspot-down.sh" "$DEST/ego-hotspot-down.sh"
fi
install -m 0644 "$SCRIPT_DIR/systemd/ecs-ego-web.service" "$SYSTEMD_USER_DIR/ecs-ego-web.service"

systemctl --user daemon-reload
systemctl --user enable --now ecs-ego-web.service

# Ensure user web service starts at boot (no interactive login).
if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "${USER}" 2>/dev/null || true
fi

echo "============================================"
echo "EGO local web deployed to $DEST"
echo "  Lab LAN:  http://10.10.10.214:8080"
echo "  Hotspot:  http://192.168.4.1:8080 (after hotspot up)"
echo "  Status:   systemctl --user status ecs-ego-web"
echo "============================================"
