#!/usr/bin/env bash
# Start LeRobot Annotate backend for Data Lab embodied labeling (:7861).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ROOT="$(cd "$SCRIPT_DIR/../../../lerobot-annotate" 2>/dev/null && pwd || true)"
LEROBOT_ANNOTATE_ROOT="${LEROBOT_ANNOTATE_ROOT:-${DEFAULT_ROOT:-/media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/lerobot-annotate}}"
PORT="${PORT:-7861}"
PID_FILE="${PID_FILE:-$SCRIPT_DIR/lerobot-annotate.pid}"
LOG_FILE="${LOG_FILE:-$SCRIPT_DIR/lerobot-annotate.log}"
VENV_UVICORN="$LEROBOT_ANNOTATE_ROOT/.venv/bin/uvicorn"

if [[ ! -x "$VENV_UVICORN" ]]; then
  echo "Missing venv uvicorn: $VENV_UVICORN" >&2
  echo "Run: cd $LEROBOT_ANNOTATE_ROOT && python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt" >&2
  exit 1
fi

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "LeRobot Annotate already running (pid $(cat "$PID_FILE"))"
  exit 0
fi

export PYTHONPATH="$LEROBOT_ANNOTATE_ROOT"
export LEROBOT_ANNOTATE_CACHE="${LEROBOT_ANNOTATE_CACHE:-$LEROBOT_ANNOTATE_ROOT/data/cache}"
export LEROBOT_ANNOTATE_EXPORT="${LEROBOT_ANNOTATE_EXPORT:-$LEROBOT_ANNOTATE_ROOT/data/exports}"

cd "$LEROBOT_ANNOTATE_ROOT"

if [[ "${LEROBOT_ANNOTATE_FOREGROUND:-}" == "1" ]]; then
  exec "$VENV_UVICORN" backend.app:app --host 0.0.0.0 --port "$PORT"
fi

nohup "$VENV_UVICORN" backend.app:app --host 0.0.0.0 --port "$PORT" >>"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"
echo "LeRobot Annotate started on :$PORT (pid $(cat "$PID_FILE"), log $LOG_FILE)"
