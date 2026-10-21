#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

echo "==> Workspace: $WORKSPACE"
echo "==> Arena branch: $ARENA_BRANCH"

command -v nvidia-smi >/dev/null || { echo "nvidia-smi not found"; exit 1; }
nvidia-smi || true

avail_gb="$(df -BG "$WORKSPACE" 2>/dev/null | awk 'NR==2 {gsub(/G/,"",$4); print $4}')"
if [[ -n "${avail_gb:-}" ]] && [[ "$avail_gb" -lt 150 ]]; then
  echo "WARN: less than 150G free on $WORKSPACE (reported ${avail_gb}G). Isaac Sim + assets need substantial disk."
fi

if ! command -v uv >/dev/null; then
  echo "Installing uv to ~/.local/bin ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

if ! command -v hf >/dev/null; then
  echo "Hugging Face CLI (hf) not found. Install: uv tool install huggingface-hub"
  echo "Then: hf auth login"
  exit 1
fi

if ! hf auth whoami >/dev/null 2>&1; then
  echo "Run: hf auth login"
  exit 1
fi

if [[ -z "${DISPLAY:-}" ]]; then
  echo "WARN: DISPLAY is unset; --viz kit may fail without a local X/Wayland session."
fi

echo "Preflight OK."
