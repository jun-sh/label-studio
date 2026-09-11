#!/usr/bin/env bash
# Prefetch Depth-Anything V2 into data-storage/cache/huggingface (via HF mirror).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=ego-pipeline-performance.env.sh
source "${SCRIPT_DIR}/ego-pipeline-performance.env.sh"
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PLATFORM_ROOT="${EGO_PLATFORM_ROOT:-$(dirname "$DATALAB_ROOT")/ego-platform}"
PIPE_ROOT="${EGO_HAND_PIPELINE_ROOT:-$(dirname "$DATALAB_ROOT")/ego-hand-pipeline}"
MODEL_ID="${EGO_DEPTH_ANYTHING_MODEL:-depth-anything/Depth-Anything-V2-Small-hf}"

PYTHON="${PIPE_ROOT}/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

mkdir -p "${HF_HOME}" "${HUGGINGFACE_HUB_CACHE}"

echo "[prefetch] model=${MODEL_ID}"
echo "[prefetch] HF_ENDPOINT=${HF_ENDPOINT}"
echo "[prefetch] HF_HOME=${HF_HOME}"

cd "$PLATFORM_ROOT"
PYTHONPATH="${PLATFORM_ROOT}/src:${PYTHONPATH:-}" \
  HF_ENDPOINT="${HF_ENDPOINT}" \
  HUGGINGFACE_HUB_ENDPOINT="${HUGGINGFACE_HUB_ENDPOINT}" \
  HF_HOME="${HF_HOME}" \
  HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE}" \
  TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE}" \
  "$PYTHON" - <<PY
import os
from transformers import AutoImageProcessor, AutoModelForDepthEstimation

model_id = ${MODEL_ID@Q}
print("downloading processor …", flush=True)
AutoImageProcessor.from_pretrained(model_id)
print("downloading model …", flush=True)
AutoModelForDepthEstimation.from_pretrained(model_id)
print("prefetch_ok", model_id, "cache=", os.environ.get("HUGGINGFACE_HUB_CACHE"))
PY
