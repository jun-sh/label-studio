#!/usr/bin/env bash
# Regression tests for hand-kp2d overlay (dual-hand + episode switch).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BRANDING="${ROOT}/data-lab-platform/lerobot-studio/branding"

echo "==> node overlay unit tests"
node --test "${BRANDING}/overlay-hand-keypoints.test.mjs"

if [[ -d "${ROOT}/ego-platform/tests" ]]; then
  echo "==> ego-platform overlay export tests"
  (cd "${ROOT}/ego-platform" && python3 -m pytest tests/test_overlay_invariants.py tests/test_append_overlay.py -q)
fi

echo "==> hand overlay tests OK"
