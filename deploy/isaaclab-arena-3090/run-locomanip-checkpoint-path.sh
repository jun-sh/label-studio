#!/usr/bin/env bash
# Locomanipulation on RTX 3090: release/0.3.1 + uv + HF checkpoint (no training, no Quest teleop).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$SCRIPT_DIR"/*.sh

"$SCRIPT_DIR/00-preflight.sh"
"$SCRIPT_DIR/01-setup-arena.sh"
"$SCRIPT_DIR/02-download-locomanip-assets.sh"
"$SCRIPT_DIR/04-setup-gr00t.sh"

echo ""
echo "=== Automated setup done ==="
echo "1) Step 1 replay (GUI):  $SCRIPT_DIR/03-run-step1-replay.sh"
echo "2) Terminal A — server:    $SCRIPT_DIR/05-run-gr00t-server.sh"
echo "3) Terminal B — eval:      $SCRIPT_DIR/06-run-step5-eval.sh"
echo ""
echo "Docs: https://isaac-sim.github.io/IsaacLab-Arena/release/0.3.1/pages/example_workflows/locomanipulation/index.html"
