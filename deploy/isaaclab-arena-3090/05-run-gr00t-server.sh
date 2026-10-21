#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

[[ -d "$CHECKPOINT_DIR" ]] || { echo "Missing checkpoint — run 02-download-locomanip-assets.sh"; exit 1; }

GR00T_DIR="$ARENA_DIR/submodules/Isaac-GR00T"
[[ -d "$GR00T_DIR" ]] || { echo "Run 04-setup-gr00t.sh first"; exit 1; }

cd "$GR00T_DIR"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

echo "Starting GR00T policy server on ${GR00T_SERVER_HOST}:${GR00T_SERVER_PORT}"
echo "Checkpoint: $CHECKPOINT_DIR"
echo "Leave this terminal running; run 06-run-step5-eval.sh in another terminal."

uv run python gr00t/eval/run_gr00t_server.py \
  --modality-config-path ../../isaaclab_arena_gr00t/embodiments/g1/g1_sim_wbc_data_config.py \
  --model-path "$CHECKPOINT_DIR" \
  --embodiment-tag NEW_EMBODIMENT \
  --device cuda \
  --host "$GR00T_SERVER_HOST" \
  --port "$GR00T_SERVER_PORT"
