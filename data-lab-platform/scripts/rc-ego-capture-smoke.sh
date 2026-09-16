#!/usr/bin/env bash
# CI gate: ego capture warm/cold + ego_web control plane unit tests.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="${SCRIPT_DIR}/.."
STREAM="${PLATFORM}/ego-stream-client"
WEB="${PLATFORM}/ego-local-web"

log() { echo "[capture-smoke] $*"; }

log "=== ego-stream-client (warm/stop contract) ==="
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest \
  "${STREAM}/tests/test_capture_stop_state.py" \
  "${STREAM}/tests/test_warm_cold_episode.py" \
  "${STREAM}/tests/test_async_frame_writer_warm_cycle.py" \
  "${STREAM}/tests/test_cli_record_oak_stream_smoke.py" \
  "${STREAM}/tests/test_capture_state.py" \
  -q

log "=== ego-local-web (control plane) ==="
python3 -m unittest discover -s "${WEB}/tests" -p 'test_*.py' -q

log "OK: capture-smoke passed"
