#!/usr/bin/env bash
# Run on 3090 (server@10.10.10.51) after sync-from-a100-to-3090.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

cd "$ARENA_DIR"
export UV_HTTP_TIMEOUT=600
export OMNI_KIT_ACCEPT_EULA=YES ACCEPT_EULA=Y

if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import isaaclab_arena' 2>/dev/null; then
  echo "==> uv sync (native rebuild; ~30-90 min, needs network)..."
  uv sync --only-group isaaclab-from-source --package isaaclab_arena 2>&1 | tee "$WORKSPACE/uv-sync-3090.log"
fi

export DISPLAY="${DISPLAY:-:0}"
exec "$SCRIPT_DIR/run-replay-gui.sh"
