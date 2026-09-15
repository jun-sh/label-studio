#!/usr/bin/env bash
# Run what we can without Unity GUI: verify bridge on 130 (same as Connect in demo).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOST="${EGO_DEMO_HOST:-10.10.10.130}"
PORT="${EGO_DEMO_PORT:-9100}"
SKIP_CAPTURE="${EGO_DEMO_SKIP_CAPTURE:-1}"

echo "=== Ego Unity Demo — headless Connect test ==="
echo "Project: $SCRIPT_DIR"
echo "Target:  $HOST:$PORT"
echo ""

if ! timeout 3 bash -c "echo > /dev/tcp/$HOST/$PORT" 2>/dev/null; then
  echo "ERROR: TCP $HOST:$PORT not reachable."
  echo "  On 130: systemctl --user status ecs-ego-tcp-bridge"
  exit 1
fi
echo "[ok] TCP port open"

TEST_PY="$ROOT/data-lab-platform/ego-local-web/tools/ego_tcp_client_test.py"
if [[ ! -f "$TEST_PY" ]]; then
  echo "ERROR: missing $TEST_PY"
  exit 1
fi

ARGS=(--host "$HOST" --port "$PORT")
if [[ "$SKIP_CAPTURE" == "1" ]]; then
  ARGS+=(--skip-capture)
fi

python3 "$TEST_PY" "${ARGS[@]}"
echo ""
echo "=== Unity GUI (on a machine with Unity 2022.3+) ==="
echo "  1. Unity Hub → Open → $SCRIPT_DIR"
echo "  2. Press Play (UI auto-builds; no menu step required)"
echo "  3. Click Connect (or add EgoDemoRuntimeBootstrap with Auto Connect On Play)"
echo ""

UNITY_BIN=""
for candidate in \
  "${UNITY_EDITOR:-}" \
  "$HOME/Unity/Hub/Editor/"*/Editor/Unity \
  /opt/Unity/Editor/Unity; do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    UNITY_BIN="$candidate"
    break
  fi
done

if [[ -n "$UNITY_BIN" ]]; then
  echo "Found Unity: $UNITY_BIN"
  echo "Launching editor (manual Play + Connect)…"
  exec "$UNITY_BIN" -projectPath "$SCRIPT_DIR"
else
  echo "Unity Editor not installed on this host — TCP protocol test above is the automated part."
fi
