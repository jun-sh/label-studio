#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${PID_FILE:-$SCRIPT_DIR/lerobot-annotate.pid}"
PORT="${PORT:-7861}"

stop_pid() {
  local pid="$1"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" || true
    echo "Stopped LeRobot Annotate (pid $pid)"
  else
    echo "Process $pid not running"
  fi
}

if [[ -f "$PID_FILE" ]]; then
  stop_pid "$(cat "$PID_FILE")"
  rm -f "$PID_FILE"
  exit 0
fi

if command -v fuser >/dev/null 2>&1; then
  if fuser "${PORT}/tcp" >/dev/null 2>&1; then
    fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
    echo "Stopped LeRobot Annotate on port $PORT"
    exit 0
  fi
fi

if command -v ss >/dev/null 2>&1; then
  pid="$(ss -tlnp "sport = :$PORT" 2>/dev/null | awk '/pid=/ {match($0,/pid=([0-9]+)/,a); print a[1]; exit}')"
  if [[ -n "${pid:-}" ]]; then
    stop_pid "$pid"
    exit 0
  fi
fi

echo "LeRobot Annotate not running on port $PORT"
exit 0
