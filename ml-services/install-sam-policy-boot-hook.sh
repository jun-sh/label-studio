#!/usr/bin/env bash
# Install user-level boot hook: enforce SAM opt-in policy after every reboot (no sudo).
#   bash ml-services/install-sam-policy-boot-hook.sh
#
# For boot without login: sudo loginctl enable-linger $(whoami)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
UNIT="data-lab-sam-policy-ensure.service"
SRC="$SCRIPT_DIR/systemd/$UNIT"
DEST_DIR="${HOME}/.config/systemd/user"
DEST="${DEST_DIR}/${UNIT}"

mkdir -p "$DEST_DIR"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
cp "$SRC" "$tmpdir/$UNIT"
sed -i "s|/media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab|${ROOT}|g" \
  "$tmpdir/$UNIT"
install -m 644 "$tmpdir/$UNIT" "$DEST"

systemctl --user daemon-reload
systemctl --user enable "$UNIT"

# Disable legacy user SAM autostart (Linger=yes would otherwise load GPU at boot).
bash "$SCRIPT_DIR/sam-backendctl.sh" disable-boot

systemctl --user start "$UNIT" || true

echo "Enabled user boot hook: $UNIT"
systemctl --user --no-pager status "$UNIT" 2>/dev/null || true
echo ""
echo "After reboot, SAM stays off GPU unless you run: bash ml-services/sam-backendctl.sh start"
echo "Optional (boot without login): sudo loginctl enable-linger $(whoami)"
echo ""
echo "Once with sudo (sync /etc SAM unit + graceful stop settings):"
echo "  sudo bash ml-services/apply-sam-systemd-policy.sh"
