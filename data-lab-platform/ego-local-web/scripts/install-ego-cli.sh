#!/usr/bin/env bash
# One-time: put ego-export / ego-upload / ego-derive-status on system PATH (/usr/local/bin).
# Run on 214 as user server (will prompt for sudo once).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EGO_WEB="${EGO_WEB_DEST:-/home/server/ego-web}"

for name in ego-export ego-upload ego-derive-status; do
  src="${EGO_WEB}/${name}"
  if [[ ! -x "$src" ]]; then
    src="${SCRIPT_DIR}/${name}"
  fi
  if [[ ! -f "$src" ]]; then
    echo "missing: $name (deploy ego-local-web first)" >&2
    exit 2
  fi
  sudo install -m 0755 "$src" "/usr/local/bin/${name}"
  echo "installed /usr/local/bin/${name}"
done

mkdir -p "${HOME}/.local/bin"
install -m 0755 "${EGO_WEB}/ego-export" "${HOME}/.local/bin/ego-export" 2>/dev/null || \
  install -m 0755 "${SCRIPT_DIR}/ego-export" "${HOME}/.local/bin/ego-export"
install -m 0755 "${EGO_WEB}/ego-upload" "${HOME}/.local/bin/ego-upload" 2>/dev/null || \
  install -m 0755 "${SCRIPT_DIR}/ego-upload" "${HOME}/.local/bin/ego-upload"
install -m 0755 "${EGO_WEB}/ego-derive-status" "${HOME}/.local/bin/ego-derive-status" 2>/dev/null || \
  install -m 0755 "${SCRIPT_DIR}/ego-derive-status" "${HOME}/.local/bin/ego-derive-status"

echo "OK — open a new terminal, then: ego-export | ego-upload | ego-derive-status"
