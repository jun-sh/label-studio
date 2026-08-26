#!/usr/bin/env bash
# Deploy ego-local-web to staging directory (8081) on edge host.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${EGO_WEB_STAGING_DEST:-/home/server/ego-web-staging}"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_NAME="${EGO_WEB_STAGING_SERVICE:-ecs-ego-web-staging.service}"

mkdir -p "$DEST" "$SYSTEMD_USER_DIR" "$DEST/templates"

install -m 0644 "$SCRIPT_DIR/ego_web.py" "$DEST/ego_web.py"
install -m 0644 "$SCRIPT_DIR/templates/capture_ui.html" "$DEST/templates/capture_ui.html"
if [[ -d "$SCRIPT_DIR/static" ]]; then
  rm -rf "$DEST/static"
  cp -a "$SCRIPT_DIR/static" "$DEST/static"
fi
install -m 0644 "$SCRIPT_DIR/export_offline.py" "$DEST/export_offline.py"
install -m 0644 "$SCRIPT_DIR/README.md" "$DEST/README.md"
install -m 0644 "$SCRIPT_DIR/systemd/${SERVICE_NAME}" "$SYSTEMD_USER_DIR/${SERVICE_NAME}"

systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}"
systemctl --user restart "${SERVICE_NAME}"

echo "============================================"
echo "EGO local web STAGING deployed to $DEST"
echo "  Staging:  http://<host>:8081"
echo "  Status:   systemctl --user status ${SERVICE_NAME}"
echo "============================================"
