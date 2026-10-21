#!/usr/bin/env bash
# Gracefully stop SAM ML backend and release GPU (SIGTERM → wait → SIGKILL).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/sam-backend.pid"
ENV_FILE="$SCRIPT_DIR/.env.sam"
PORT=9090
GRACE_SEC="${SAM_STOP_GRACE_SEC:-90}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  PORT="${PORT:-9090}"
fi

stop_pid_graceful() {
  local pid="$1"
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Process $pid not running"
    return 0
  fi
  echo "Stopping SAM backend (pid $pid, grace ${GRACE_SEC}s)..."
  kill -TERM "$pid" 2>/dev/null || true
  local waited=0
  while kill -0 "$pid" 2>/dev/null; do
    if (( waited >= GRACE_SEC )); then
      echo "SIGTERM timeout — sending SIGKILL to pid $pid"
      kill -KILL "$pid" 2>/dev/null || true
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "SAM backend stopped (pid $pid)"
}

find_listener_pid() {
  if command -v ss >/dev/null 2>&1; then
    ss -tlnp "sport = :$PORT" 2>/dev/null \
      | awk '/pid=/ {match($0,/pid=([0-9]+)/,a); print a[1]; exit}'
  fi
}

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"
  stop_pid_graceful "$pid"
  rm -f "$PID_FILE"
  exit 0
fi

pid="$(find_listener_pid || true)"
if [[ -n "${pid:-}" ]]; then
  stop_pid_graceful "$pid"
  exit 0
fi

if command -v fuser >/dev/null 2>&1 && fuser "${PORT}/tcp" >/dev/null 2>&1; then
  echo "No pid resolved — asking fuser to release port $PORT"
  fuser -k -TERM "${PORT}/tcp" >/dev/null 2>&1 || true
  sleep 3
  fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
  echo "Stopped listener on port $PORT"
  exit 0
fi

echo "SAM backend not running (no pid file, port $PORT free)"
exit 0
