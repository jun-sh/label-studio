#!/usr/bin/env bash
# Two-cycle warm cold episode test on 130 via local HTTP API (run on 130 or via SSH).
# Usage: bash ego-130-test-warm-cold-episode.sh [web_base]
set -euo pipefail

WEB="${1:-http://127.0.0.1:8080}"
POLL_S=0.5
RECORD_HOLD_S="${EGO_WARM_TEST_HOLD_S:-8}"
STOP_WAIT_S=120
START_TIMEOUT_S=120

poll_status() {
  curl -sf "${WEB}/api/status"
}

wait_state() {
  local want="$1"
  local timeout="$2"
  local since="${3:-0}"
  local deadline=$(( $(date +%s) + timeout ))
  while [[ $(date +%s) -lt $deadline ]]; do
    local st msg
    st=$(poll_status | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('state',''))")
    msg=$(poll_status | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('msg',''))" 2>/dev/null || true)
    if [[ "$st" == "$want" ]]; then
      echo "state=${st} msg=${msg}"
      return 0
    fi
    sleep "$POLL_S"
  done
  echo "TIMEOUT waiting for state=${want} (last=$(poll_status))" >&2
  return 1
}

click_start() {
  local t0
  t0=$(date +%s.%N)
  curl -sf -X POST "${WEB}/api/capture/start" >/dev/null
  if wait_state "recording" "$START_TIMEOUT_S"; then
    local t1
    t1=$(date +%s.%N)
    python3 - <<PY
t0=float("${t0}"); t1=float("${t1}")
print(f"start_to_recording_s={t1-t0:.2f}")
PY
    return 0
  fi
  return 1
}

click_stop() {
  curl -sf -X POST "${WEB}/api/capture/stop" >/dev/null
  wait_state "idle" "$STOP_WAIT_S"
}

echo "==> Warm cold episode test ${WEB}"
cur=$(poll_status)
state=$(echo "$cur" | python3 -c "import sys,json; print(json.load(sys.stdin).get('state',''))")
if [[ "$state" == "recording" || "$state" == "starting" || "$state" == "stopping" ]]; then
  echo "BLOCKED: capture busy state=${state} — stop recording first, then re-run." >&2
  exit 2
fi

echo "--- Cycle 1 (cold start expected ~15-20s) ---"
click_start
sleep "$RECORD_HOLD_S"
click_stop
sleep 3

warm=$(poll_status | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('oak_warm_active', False))")
echo "after_stop oak_warm_active=${warm}"

echo "--- Cycle 2 (warm start expected ~2-5s) ---"
click_start
sleep "$RECORD_HOLD_S"
click_stop

echo "==> Test complete"
