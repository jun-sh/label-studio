#!/usr/bin/env bash
# Monitor 30Hz soak until TARGET_SEGMENTS closed, then export/upload/verify.
set -euo pipefail

TARGET_SEGMENTS="${TARGET_SEGMENTS:-12}"
TARGET_FRAMES="${TARGET_FRAMES:-$(( TARGET_SEGMENTS * 300 ))}"
POLL_S="${POLL_S:-5}"
SEG_ROOT="${EGO_SEGMENT_ROOT:-/home/server/cache/ego-lan-214/segments}"
LOG="${EGO_SOAK_LOG:-/home/server/cache/ego-lan-214/logs/soak-30hz-$(date +%Y%m%d-%H%M%S).log}"
DATALAB_BASE="${EGO_DATALAB_BASE_URL:-http://10.10.10.34:8080}"

mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1

log() { echo "[$(date +%H:%M:%S)] $*"; }

count_closed_segments() {
  local sess="$1"
  find "${SEG_ROOT}/sessions/${sess}/segments" -name manifest.json 2>/dev/null \
    | while read -r m; do
        python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get('closed') else 1)" "$m" 2>/dev/null && echo 1
      done | wc -l
}

get_session_id() {
  python3 -c "import json; print(json.load(open('${SEG_ROOT}/checkpoint.json')).get('sessionId',''))" 2>/dev/null || true
}

get_captured_frames() {
  journalctl --user -u ecs-record-oak-stream -n 1 --no-pager 2>/dev/null \
    | grep -oE 'captured=[0-9]+' | tail -1 | cut -d= -f2 || echo 0
}

log "soak monitor: target=${TARGET_SEGMENTS} segs / ${TARGET_FRAMES} frames (~$(( TARGET_SEGMENTS * 10 ))s @30Hz)"

while systemctl --user is-active ecs-record-oak-stream.service >/dev/null 2>&1; do
  sess="$(get_session_id)"
  n=0
  [[ -n "$sess" ]] && n="$(count_closed_segments "$sess" | tr -d ' ')"
  frames="$(get_captured_frames)"
  fps_line="$(journalctl --user -u ecs-record-oak-stream -n 1 --no-pager 2>/dev/null | grep capture_fps || true)"
  log "session=${sess:-?} closed=${n} frames=${frames}/${TARGET_FRAMES} ${fps_line##*: }"
  if [[ "${frames:-0}" -ge "${TARGET_FRAMES}" ]] || [[ "${n:-0}" -ge "${TARGET_SEGMENTS}" ]]; then
    log "target reached — stopping capture"
    systemctl --user stop ecs-oak-capture-stack.target || true
    sleep 5
    break
  fi
  sleep "$POLL_S"
done

sess="$(get_session_id)"
[[ -n "$sess" ]] || die "no session in checkpoint"
final_n="$(count_closed_segments "$sess" | tr -d ' ')"
log "final closed segments: ${final_n}"

STUDIO="${EGO_STUDIO:-/home/server/workspace/ego-studio}"
VAL="${STUDIO}/src/ego_capture_studio/tools/validate_strict_sync.py"
if [[ -f "$VAL" ]]; then
  log "=== validate_strict_sync ==="
  "${STUDIO}/.venv/bin/python" "$VAL" \
    --batch-dir "${SEG_ROOT}/sessions/${sess}/segments" --interval-ms 33 \
    || log "WARN: timing validation had failures"
fi

log "=== ego-export ==="
if command -v ego-export >/dev/null 2>&1; then
  ego-export
else
  bash /home/server/ego-web/export-offline.sh
fi

log "=== ego-upload ==="
export EGO_DATALAB_BASE_URL="$DATALAB_BASE"
if command -v ego-upload >/dev/null 2>&1; then
  ego-upload
else
  die "ego-upload not found"
fi

log "=== derive status (34) ==="
if command -v ego-derive-status >/dev/null 2>&1; then
  for i in $(seq 1 60); do
    out="$(ego-derive-status 2>/dev/null || true)"
    echo "$out"
    echo "$out" | grep -q "已就绪" && break
    phase="$(curl -s "${DATALAB_BASE}/lerobot/api/collection/stations/ego-lan-214/derive-status" | python3 -c "import sys,json; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null || true)"
    [[ "$phase" == "READY" ]] && break
    sleep 10
  done
fi

log "=== done. log: $LOG ==="
