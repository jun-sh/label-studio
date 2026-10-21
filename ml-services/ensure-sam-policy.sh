#!/usr/bin/env bash
# Enforce SAM opt-in GPU policy (default: no boot autostart, stop if left running after reboot).
# Safe to run at boot as user01; root not required unless starting via system unit.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.sam"
SAM_BOOT_AUTOSTART=0

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  SAM_BOOT_AUTOSTART="${SAM_BOOT_AUTOSTART:-0}"
fi

if [[ "$SAM_BOOT_AUTOSTART" == "1" ]]; then
  exit 0
fi

exec bash "$SCRIPT_DIR/sam-backendctl.sh" disable-boot
