#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

[[ -d "$ARENA_DIR" ]] || { echo "Run 01-setup-arena.sh first"; exit 1; }

cd "$ARENA_DIR"
git submodule update --init submodules/Isaac-GR00T

GR00T_DIR="$ARENA_DIR/submodules/Isaac-GR00T"
cd "$GR00T_DIR"

# Pin matches Isaac Lab-Arena release/0.3.1 locomanipulation evaluation docs.
git fetch --tags 2>/dev/null || true
git checkout e29d8fc 2>/dev/null || git checkout "$(git rev-parse HEAD)"

echo "==> Syncing Isaac-GR00T uv environment (may take a while; flash-attn compiles on first install)"
if ! uv sync; then
  echo ""
  echo "If flash-attn build failed on RTX 3090, retry with:"
  echo "  cd $GR00T_DIR"
  echo "  MAX_JOBS=4 uv sync"
  echo "Or see NVIDIA/Isaac-GR00T install notes for your driver/CUDA combo."
  exit 1
fi

echo "GR00T env ready at $GR00T_DIR/.venv"
