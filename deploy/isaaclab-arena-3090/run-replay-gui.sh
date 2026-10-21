#!/usr/bin/env bash
# Locomanipulation Step 1 — Isaac Sim GUI replay (3090 / server@...)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

export DISPLAY="${DISPLAY:-:0}"

DATASET_FILE="$DATASET_DIR/arena_g1_loco_manipulation_dataset_generated_small.hdf5"
if [[ ! -f "$DATASET_FILE" ]]; then
  echo "Missing dataset: $DATASET_FILE"
  echo "Run: $SCRIPT_DIR/02-download-locomanip-assets.sh"
  exit 1
fi
if [[ ! -x "$ARENA_DIR/.venv/bin/python" ]]; then
  echo "Arena venv not found: $ARENA_DIR/.venv"
  echo "Run: $SCRIPT_DIR/01-setup-arena.sh  (and complete uv sync; see README-3090.txt)"
  exit 1
fi

cd "$ARENA_DIR"
export OMNI_KIT_ACCEPT_EULA=YES ACCEPT_EULA=Y
export PYTHONPATH="$ARENA_DIR${PYTHONPATH:+:$PYTHONPATH}"
# Warp JIT cache (WARP_CACHE_PATH; avoid root-owned ~/.cache/warp from prior sudo runs)
export WARP_CACHE_PATH="${WARP_CACHE_PATH:-$WORKSPACE/.cache/warp}"
mkdir -p "$WARP_CACHE_PATH"

exec "$ARENA_DIR/.venv/bin/python" submodules/IsaacLab/scripts/tools/replay_demos.py \
  --viz kit \
  --device cpu \
  --enable_cameras \
  --dataset_file "$DATASET_FILE" \
  --external_callback isaaclab_arena.environments.isaaclab_interop.environment_registration_callback \
  --task galileo_g1_locomanip_pick_and_place \
  --object brown_box \
  --embodiment g1_wbc_pink
