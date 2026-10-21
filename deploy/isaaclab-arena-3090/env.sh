# Source from other scripts: source "$(dirname "$0")/env.sh"
export WORKSPACE="${WORKSPACE:-/home/server/workspace}"
export ARENA_BRANCH="${ARENA_BRANCH:-release/0.3.1}"
if [[ -d "${WORKSPACE}/isaaclab-arena-run/IsaacLab-Arena" ]]; then
  export ARENA_DIR="${ARENA_DIR:-$WORKSPACE/isaaclab-arena-run/IsaacLab-Arena}"
  export DATASET_DIR="${DATASET_DIR:-$WORKSPACE/isaaclab-arena-run/datasets/isaaclab_arena/locomanipulation_tutorial}"
else
  export ARENA_DIR="${ARENA_DIR:-$WORKSPACE/IsaacLab-Arena}"
  export DATASET_DIR="${DATASET_DIR:-$WORKSPACE/datasets/isaaclab_arena/locomanipulation_tutorial}"
fi
export MODELS_DIR="${MODELS_DIR:-$WORKSPACE/models/isaaclab_arena/locomanipulation_tutorial}"
export CHECKPOINT_DIR="${CHECKPOINT_DIR:-$MODELS_DIR/checkpoint-20000}"

export HF_DATASET_REPO="nvidia/Arena-G1-Loco-Manipulation-Task"
export HF_DATASET_REVISION="arena_v0.2_lab_v3.0"
export HF_CHECKPOINT_REPO="nvidia/GN1x-Tuned-Arena-G1-Loco-Manipulation"
export HF_CHECKPOINT_REVISION="gn1_6"

export OMNI_KIT_ACCEPT_EULA=YES
export ACCEPT_EULA=Y

# China network: Isaac Sim 6.1 assets via SimReady CDN (see isaaclab.utils.assets)
export ISAACSIM_ASSET_REGION_PROFILE="${ISAACSIM_ASSET_REGION_PROFILE:-china}"
export WARP_CACHE_PATH="${WARP_CACHE_PATH:-$WORKSPACE/.cache/warp}"

export GR00T_SERVER_HOST="${GR00T_SERVER_HOST:-127.0.0.1}"
export GR00T_SERVER_PORT="${GR00T_SERVER_PORT:-5555}"

mkdir -p "$DATASET_DIR" "$MODELS_DIR" "$CHECKPOINT_DIR"
