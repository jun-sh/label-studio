#!/usr/bin/env bash
# Deploy EGO local web control to 214 (run on edge host as user server).
# Polkit + hotspot steps need one-time sudo — see README.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${EGO_WEB_DEST:-/home/server/ego-web}"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$DEST" "$SYSTEMD_USER_DIR" "$DEST/templates"

install -m 0644 "$SCRIPT_DIR/ego_web.py" "$DEST/ego_web.py"
install -m 0644 "$SCRIPT_DIR/templates/capture_ui.html" "$DEST/templates/capture_ui.html"
if [[ -d "$SCRIPT_DIR/static" ]]; then
  rm -rf "$DEST/static"
  cp -a "$SCRIPT_DIR/static" "$DEST/static"
fi
install -m 0644 "$SCRIPT_DIR/export_offline.py" "$DEST/export_offline.py"
install -m 0755 "$SCRIPT_DIR/scripts/export-offline.sh" "$DEST/export-offline.sh"
install -m 0755 "$SCRIPT_DIR/scripts/ego-export" "$DEST/ego-export"
install -m 0755 "$SCRIPT_DIR/scripts/ego-upload" "$DEST/ego-upload"
install -m 0755 "$SCRIPT_DIR/scripts/ego-derive-status" "$DEST/ego-derive-status"
install -m 0755 "$SCRIPT_DIR/scripts/install-ego-cli.sh" "$DEST/install-ego-cli.sh"
mkdir -p "$DEST/lib/ego-stream-client/cli"
install -m 0644 "$SCRIPT_DIR/../ego-stream-client/derive_status.py" "$DEST/lib/ego-stream-client/derive_status.py"
install -m 0644 "$SCRIPT_DIR/../ego-stream-client/cli/derive_status_cli.py" "$DEST/lib/ego-stream-client/cli/derive_status_cli.py"
mkdir -p "${HOME}/.local/bin"
install -m 0755 "$SCRIPT_DIR/scripts/ego-export" "${HOME}/.local/bin/ego-export"
install -m 0755 "$SCRIPT_DIR/scripts/ego-upload" "${HOME}/.local/bin/ego-upload"
install -m 0755 "$SCRIPT_DIR/scripts/ego-derive-status" "${HOME}/.local/bin/ego-derive-status"
if command -v sudo >/dev/null 2>&1; then
  if [[ -x "$SCRIPT_DIR/scripts/install-ego-cli.sh" ]]; then
    echo "Installing ego-export / ego-upload / ego-derive-status to /usr/local/bin (sudo once)…"
    bash "$SCRIPT_DIR/scripts/install-ego-cli.sh" || \
      echo "Tip: run manually: sudo bash $SCRIPT_DIR/scripts/install-ego-cli.sh"
  fi
fi
install -m 0644 "$SCRIPT_DIR/README.md" "$DEST/README.md"
install -m 0644 "$SCRIPT_DIR/field-export-and-import.md" "$DEST/field-export-and-import.md"
if [[ -f /home/server/ego-web/ego-hotspot-up.sh ]]; then
  :
elif [[ -f "$SCRIPT_DIR/scripts/ego-hotspot-up.sh" ]]; then
  install -m 0755 "$SCRIPT_DIR/scripts/ego-hotspot-up.sh" "$DEST/ego-hotspot-up.sh"
  install -m 0755 "$SCRIPT_DIR/scripts/ego-hotspot-down.sh" "$DEST/ego-hotspot-down.sh"
fi
install -m 0644 "$SCRIPT_DIR/systemd/ecs-ego-web.service" "$SYSTEMD_USER_DIR/ecs-ego-web.service"

# Remove legacy auto-export module if present.
rm -f "$DEST/ego_auto_export.py"

systemctl --user daemon-reload
systemctl --user enable ecs-ego-web.service
systemctl --user restart ecs-ego-web.service

# Ensure user web service starts at boot (no interactive login).
if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "${USER}" 2>/dev/null || true
fi

echo "============================================"
echo "EGO local web deployed to $DEST"
echo "  Lab LAN:  http://10.10.10.214:8080"
echo "  Hotspot:  http://192.168.4.1:8080 (after hotspot up)"
echo "  Export:   ego-export   (或 $DEST/export-offline.sh)"
echo "  Upload:   ego-upload   (上传到 34，终端实时进度)"
echo "  Derive:   ego-derive-status   (查 34 派生进度)"
echo "  Status:   systemctl --user status ecs-ego-web"
echo "============================================"
