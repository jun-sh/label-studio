#!/usr/bin/env bash
# Deploy ego change_view test web to dev directory (8082) on edge host.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${EGO_WEB_DEV_DEST:-/home/server/ego-web-dev}"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_NAME="${EGO_WEB_DEV_SERVICE:-ecs-ego-web-dev.service}"

mkdir -p "$DEST" "$SYSTEMD_USER_DIR" "$DEST/templates"

install -m 0644 "$SCRIPT_DIR/ego_web.py" "$DEST/ego_web.py"
install -m 0644 "$SCRIPT_DIR/templates/change_view_test.html" "$DEST/templates/change_view_test.html"
install -m 0644 "$SCRIPT_DIR/templates/quad_preview.html" "$DEST/templates/quad_preview.html"
install -m 0644 "$SCRIPT_DIR/templates/capture_ui.html" "$DEST/templates/capture_ui.html"
if [[ -d "$SCRIPT_DIR/static" ]]; then
  rm -rf "$DEST/static"
  cp -a "$SCRIPT_DIR/static" "$DEST/static"
fi
install -m 0644 "$SCRIPT_DIR/README.md" "$DEST/README.md"
install -m 0644 "$SCRIPT_DIR/systemd/${SERVICE_NAME}" "$SYSTEMD_USER_DIR/${SERVICE_NAME}"

if [[ -f "$SCRIPT_DIR/systemd/ecs-ego-web-dev.service.d/z-mcap-production.conf" ]]; then
  mkdir -p "$SYSTEMD_USER_DIR/${SERVICE_NAME}.d"
  install -m 0644 \
    "$SCRIPT_DIR/systemd/ecs-ego-web-dev.service.d/z-mcap-production.conf" \
    "$SYSTEMD_USER_DIR/${SERVICE_NAME}.d/z-mcap-production.conf"
elif [[ -f "$SCRIPT_DIR/systemd/ecs-ego-web-staging.service.d/z-mcap-production.conf" ]]; then
  mkdir -p "$SYSTEMD_USER_DIR/${SERVICE_NAME}.d"
  install -m 0644 \
    "$SCRIPT_DIR/systemd/ecs-ego-web-staging.service.d/z-mcap-production.conf" \
    "$SYSTEMD_USER_DIR/${SERVICE_NAME}.d/z-mcap-production.conf"
fi

systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}"
systemctl --user restart "${SERVICE_NAME}"

echo "============================================"
echo "EGO change_view test deployed to $DEST"
echo "  Dev UI:   http://127.0.0.1:8082/  (LAN: http://<host>:8082)"
echo "  API:      POST /api/change_view  {\"view_num\":1..4}"
echo "  Status:   systemctl --user status ${SERVICE_NAME}"
echo "============================================"
