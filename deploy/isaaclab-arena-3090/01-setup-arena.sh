#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

# Submodule URLs in .gitmodules use git@; HTTPS clone avoids SSH key requirements.
git config --global url."https://github.com/".insteadOf "git@github.com:" || true

if [[ ! -d "$ARENA_DIR/.git" ]]; then
  git clone --branch "$ARENA_BRANCH" --recurse-submodules \
    https://github.com/isaac-sim/IsaacLab-Arena.git "$ARENA_DIR"
else
  cd "$ARENA_DIR"
  git fetch origin "$ARENA_BRANCH"
  git checkout "$ARENA_BRANCH"
  git submodule sync --recursive
  git submodule update --init --recursive
fi

cd "$ARENA_DIR"
uv sync --extra dev

echo ""
echo "Arena ready. Activate with:"
echo "  source $ARENA_DIR/.venv/bin/activate"
echo "  export OMNI_KIT_ACCEPT_EULA=YES ACCEPT_EULA=Y"
