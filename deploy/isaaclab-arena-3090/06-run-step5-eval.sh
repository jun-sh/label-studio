#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

cd "$ARENA_DIR"
# shellcheck disable=SC1091
source .venv/bin/activate
export OMNI_KIT_ACCEPT_EULA=YES ACCEPT_EULA=Y

# Dataset was generated with CPU physics; use --device cpu for sim reproducibility (release/0.3.1 docs).
python isaaclab_arena/evaluation/policy_runner.py \
  --viz kit \
  --device cpu \
  --policy_type isaaclab_arena_gr00t.policy.gr00t_remote_closedloop_policy.Gr00tRemoteClosedloopPolicy \
  --policy_config_yaml_path isaaclab_arena_gr00t/policy/config/g1_locomanip_gr00t_closedloop_config.yaml \
  --remote_host "$GR00T_SERVER_HOST" \
  --remote_port "$GR00T_SERVER_PORT" \
  --num_steps 1500 \
  --enable_cameras \
  galileo_g1_locomanip_pick_and_place \
  --object brown_box \
  --embodiment g1_wbc_joint
