#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

DATASET_FILE="$DATASET_DIR/arena_g1_loco_manipulation_dataset_generated_small.hdf5"
[[ -f "$DATASET_FILE" ]] || { echo "Missing $DATASET_FILE — run 02-download-locomanip-assets.sh"; exit 1; }

cd "$ARENA_DIR"
export OMNI_KIT_ACCEPT_EULA=YES ACCEPT_EULA=Y
export PYTHONPATH="$ARENA_DIR${PYTHONPATH:+:$PYTHONPATH}"

"$ARENA_DIR/.venv/bin/python" submodules/IsaacLab/scripts/tools/replay_demos.py \
  --viz kit \
  --device cpu \
  --enable_cameras \
  --dataset_file "$DATASET_FILE" \
  --external_callback isaaclab_arena.environments.isaaclab_interop.environment_registration_callback \
  --task galileo_g1_locomanip_pick_and_place \
  --object brown_box \
  --embodiment g1_wbc_pink
