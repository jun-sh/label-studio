#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$SCRIPT_DIR/env.sh"

echo "==> Step 1 validation dataset (small HDF5)"
hf download \
  "$HF_DATASET_REPO" \
  arena_g1_loco_manipulation_dataset_generated_small.hdf5 \
  --repo-type dataset \
  --revision "$HF_DATASET_REVISION" \
  --local-dir "$DATASET_DIR"

echo "==> Pre-trained GR00T checkpoint (skip training)"
hf download \
  --revision "$HF_CHECKPOINT_REVISION" \
  "$HF_CHECKPOINT_REPO" \
  --local-dir "$CHECKPOINT_DIR"

echo "Assets under:"
echo "  $DATASET_DIR"
echo "  $CHECKPOINT_DIR"
