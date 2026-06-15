#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/sam-backend.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No pid file — backend not running?"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID" || true
  echo "Stopped SAM backend (pid $PID)"
else
  echo "Process $PID not running"
fi
rm -f "$PID_FILE"
