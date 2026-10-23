#!/usr/bin/env bash
# Poll ego-002 upload + local 34 derive markers during large upload observe run.
set -euo pipefail
INTERVAL="${EGO_OBSERVE_INTERVAL:-60}"
LOG="${EGO_OBSERVE_LOG:-$(cd "$(dirname "$0")/../.." && pwd)/data-storage/logs/ego-002-upload-observe-20261022-poll.log}"
STATION=ego-002
MAIN_SESS=sess_2c91b2e1e4de4dc58da1c5da5d384984
STREAM_ROOT="${DATALAB_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}/data-storage/stream/${STATION}"
SSH_HOST="${EGO_002_SSH:-server@10.10.11.52}"
SSHPASS="${EGO_002_SSH_PASS:-}"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*" | tee -a "$LOG"; }

ssh_002() {
  if [[ -n "$SSHPASS" ]]; then
    SSHPASS="$SSHPASS" sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 "$SSH_HOST" "$@"
  else
    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 "$SSH_HOST" "$@"
  fi
}

log "monitor start interval=${INTERVAL}s log=$LOG"

while true; do
  edge="$(ssh_002 'pgrep -af "ego_upload|ego-upload" 2>/dev/null | head -3; pending=$(find ~/cache/ego-002/segments/sessions/sess_2c91b2e1e4de4dc58da1c5da5d384984/segments -maxdepth 1 -type d -name "seg_*" 2>/dev/null | wc -l); du -sh ~/cache/ego-002/segments/sessions/sess_2c91b2e1e4de4dc58da1c5da5d384984 2>/dev/null; echo "local_segs=$pending"; tail -1 ~/cache/ego-002/logs/ego-upload-observe-*.log 2>/dev/null || tail -1 ~/cache/ego-002/logs/ego-upload-*.log 2>/dev/null' 2>&1)" || edge="ssh_failed"

  raw_n=0
  markers=""
  if [[ -d "${STREAM_ROOT}/raw/segments/${MAIN_SESS}" ]]; then
    raw_n=$(find "${STREAM_ROOT}/raw/segments/${MAIN_SESS}" \( -name '*.tar.zst' -o -name '*.mcap.zst' -o -name '*.mcap' \) 2>/dev/null | wc -l)
  fi
  for m in session.DONE_UPLOAD session.DERIVING session.READY session.FAILED; do
    [[ -f "${STREAM_ROOT}/state/sessions/${MAIN_SESS}/${m}" ]] && markers+="${m} "
  done
  prog=""
  pf="${STREAM_ROOT}/live/derive/progress/${MAIN_SESS}.json"
  if [[ -f "$pf" ]]; then
    prog=$(python3 -c "import json; d=json.load(open('$pf')); print(d.get('phase'), d.get('done'), d.get('total'))" 2>/dev/null || true)
  fi

  log "edge: ${edge//$'\n'/ | }"
  log "34: raw_tarzst=${raw_n} markers=[${markers:-none}] derive_progress=${prog:-n/a}"
  sleep "$INTERVAL"
done
