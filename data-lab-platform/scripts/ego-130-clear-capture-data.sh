#!/usr/bin/env bash
# Clear ego-001 edge capture cache on 130 (segments, checkpoint, tmpfs active). Does NOT touch export/.
# Usage: RC_CAPTURE_PASS=1 bash ego-130-clear-capture-data.sh [server@10.10.10.130] [station_id]
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
STATION_ID="${2:-ego-001}"
CACHE_ROOT="/home/server/cache/${STATION_ID}"

_run_remote() {
  local script="$1"
  if ssh -o BatchMode=yes -o ConnectTimeout=8 "${TARGET}" "bash -s" <<< "$script" 2>/dev/null; then
    return 0
  fi
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" TARGET="$TARGET" REMOTE_BODY="$script" python3 - <<'PY'
import os, paramiko, sys
target = os.environ["TARGET"]
user, _, host = target.partition("@")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=20)
_, stdout, stderr = c.exec_command(f"bash -s <<'REMOTE'\n{os.environ['REMOTE_BODY']}\nREMOTE")
sys.stdout.write(stdout.read().decode())
sys.stderr.write(stderr.read().decode())
raise SystemExit(stdout.channel.recv_exit_status())
PY
}

echo "==> Clear capture data on ${TARGET} station=${STATION_ID}"

_run_remote "$(cat <<REMOTE
set -euo pipefail
STATION_ID="${STATION_ID}"
CACHE_ROOT="${CACHE_ROOT}"
ACTIVE_ROOTS=(
  "\${CACHE_ROOT}/../ego-001-mcap-active"
  "/dev/shm/ego-001-mcap-active"
  "/tmp/ego-001-mcap-active"
)

systemctl --user stop ecs-oak-mcap-capture-stack.target 2>/dev/null || true
systemctl --user stop ecs-record-oak-mcap.service 2>/dev/null || true
systemctl --user reset-failed ecs-record-oak-mcap.service 2>/dev/null || true

if [[ -d "\${CACHE_ROOT}/segments/sessions" ]]; then
  rm -rf "\${CACHE_ROOT}/segments/sessions/"*
fi
rm -f "\${CACHE_ROOT}/checkpoint.json" "\${CACHE_ROOT}/capture_live_stats.json" \
  "\${CACHE_ROOT}/oak_warm_state.json" "\${CACHE_ROOT}/capture_stop_state.json" \
  "\${CACHE_ROOT}/strict_emit_ts.json" 2>/dev/null || true

for root in "\${ACTIVE_ROOTS[@]}"; do
  if [[ -d "\${root}" ]]; then
    rm -rf "\${root}/"*
  fi
done

echo "cleared station=\${STATION_ID} cache=\${CACHE_ROOT}"
echo "segment_sessions=\$(ls -1 \${CACHE_ROOT}/segments/sessions 2>/dev/null | wc -l)"
REMOTE
)"

echo "==> Done."
