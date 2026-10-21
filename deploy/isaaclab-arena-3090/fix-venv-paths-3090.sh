#!/usr/bin/env bash
# After rsyncing .venv from A100 (user01 paths), rewrite editable-install paths for 3090.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

OLD_PREFIX="${OLD_PREFIX:-/home/user01/workspace/isaaclab-arena-run}"
NEW_PREFIX="${NEW_PREFIX:-$WORKSPACE/isaaclab-arena-run}"
VENV="${VENV:-$ARENA_DIR/.venv}"

if [[ ! -d "$VENV" ]]; then
  echo "Missing venv: $VENV"
  exit 1
fi

echo "==> Rewriting $OLD_PREFIX -> $NEW_PREFIX under $VENV"
mapfile -t files < <(grep -rl "$OLD_PREFIX" "$VENV" 2>/dev/null || true)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "No references to OLD_PREFIX; nothing to do."
  exit 0
fi
for f in "${files[@]}"; do
  sed -i "s|$OLD_PREFIX|$NEW_PREFIX|g" "$f"
done
echo "==> Patched ${#files[@]} files."
echo "==> Smoke test:"
export PYTHONPATH="$ARENA_DIR${PYTHONPATH:+:$PYTHONPATH}"
"$VENV/bin/python" -c "import isaaclab; import isaaclab_arena; print('imports_ok')"
