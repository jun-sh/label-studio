#!/usr/bin/env bash
# Install ego-process-watcher + boot preflight systemd units (34 platform host).
#   sudo bash data-lab-platform/scripts/install-ego-process-watcher-systemd.sh
#   bash data-lab-platform/scripts/install-ego-process-watcher-systemd.sh --user
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SYSTEMD_SRC="${SCRIPT_DIR}/../systemd"
MODE="${1:-system}"

install_units() {
  local dest_dir="$1"
  local ctl="$2"
  local linger_hint="${3:-}"
  local unit_mode="${4:-system}"

  for unit in \
    data-lab-ego-process-watcher.service \
    data-lab-ego-process-watcher.timer \
    data-lab-ego-preflight.service \
    data-lab-ego-preflight.timer; do
    install -m 644 "${SYSTEMD_SRC}/${unit}" "${dest_dir}/${unit}"
  done

  # Patch absolute paths for this checkout (units ship with default workspace path).
  sed -i "s|/media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab|${ROOT}|g" \
    "${dest_dir}"/data-lab-ego-*.service
  if [[ "$unit_mode" == "user" ]]; then
    # User units already run as the logged-in user; User=/Group= breaks with status=216/GROUP.
    sed -i '/^User=/d; /^Group=/d' "${dest_dir}"/data-lab-ego-*.service
  fi

  ${ctl} daemon-reload
  ${ctl} enable data-lab-ego-process-watcher.timer
  ${ctl} enable data-lab-ego-preflight.timer
  ${ctl} start data-lab-ego-process-watcher.timer
  ${ctl} start data-lab-ego-preflight.timer
  ${ctl} --no-pager status data-lab-ego-process-watcher.timer || true
  echo ""
  echo "Enabled timers. Logs:"
  echo "  journalctl ${linger_hint}-u data-lab-ego-process-watcher.service -f"
  echo "  journalctl ${linger_hint}-u data-lab-ego-preflight.service -f"
}

if [[ "$MODE" == "--user" ]]; then
  DEST_DIR="${HOME}/.config/systemd/user"
  mkdir -p "${DEST_DIR}"
  install_units "${DEST_DIR}" "systemctl --user" "--user" "user"
  echo ""
  echo "User units enabled. For boot without login:"
  echo "  sudo loginctl enable-linger $(whoami)"
  exit 0
fi

if [[ "$MODE" != "system" ]]; then
  echo "Usage: sudo bash $0 | bash $0 --user" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo: sudo bash $0" >&2
  exit 1
fi

install_units "/etc/systemd/system" "systemctl" ""
echo "Done."
