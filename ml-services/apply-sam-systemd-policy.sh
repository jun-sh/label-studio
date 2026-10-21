#!/usr/bin/env bash
# Sync SAM systemd unit from repo and apply production default (no boot autostart).
#   sudo bash ml-services/apply-sam-systemd-policy.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
UNIT_NAME="data-lab-sam-backend.service"
SRC="$SCRIPT_DIR/systemd/$UNIT_NAME"
DEST="/etc/systemd/system/$UNIT_NAME"
ENV_FILE="$SCRIPT_DIR/.env.sam"
SAM_BOOT_AUTOSTART=0

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo: sudo bash $0" >&2
  exit 1
fi

if [[ ! -f "$SRC" ]]; then
  echo "Missing $SRC" >&2
  exit 1
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
cp "$SRC" "$tmpdir/$UNIT_NAME"
sed -i "s|/media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab|${ROOT}|g" \
  "$tmpdir/$UNIT_NAME"
install -m 644 "$tmpdir/$UNIT_NAME" "$DEST"
systemctl daemon-reload

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  SAM_BOOT_AUTOSTART="${SAM_BOOT_AUTOSTART:-0}"
fi

if [[ "$SAM_BOOT_AUTOSTART" == "1" ]]; then
  systemctl enable "$UNIT_NAME"
  echo "SAM_BOOT_AUTOSTART=1 — unit enabled for boot (not started unless you start it)."
else
  systemctl disable "$UNIT_NAME" 2>/dev/null || true
  systemctl stop "$UNIT_NAME" 2>/dev/null || true
  echo "SAM opt-in policy applied: unit synced, boot disabled, service stopped."
fi
