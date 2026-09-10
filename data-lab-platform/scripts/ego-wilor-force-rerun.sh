#!/usr/bin/env bash
# Force re-convert all ego-001 sessions with WiLoR (post HAMER→WiLoR migration).
set -euo pipefail

DATALAB_ROOT="/media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab"
PIPE_ROOT="/media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/ego-hand-pipeline"
PLATFORM_ROOT="/media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/ego-platform"
PY="$PIPE_ROOT/.venv/bin/python3"
LOG="/tmp/ego-wilor-rerun-$(date +%Y%m%d-%H%M%S).log"

export WILOR_ROOT="$PIPE_ROOT/third_party/wilor"
export WILOR_IMPL=repo
export EGO_WILOR_BACKEND=repo
export EGO_WILOR_DEVICE="${EGO_WILOR_DEVICE:-cuda:0}"
export EGO_HANDS_FORCE=1
export DATALAB_ROOT
unset EGO_HANDS_MAX_FRAMES

SESSIONS=(
  sess_acf0eddfaa984a4a84df99e43508cf97
  sess_4eacffc281e74298b18028c98e22cb6d
  sess_5c2c64cb0040450c8cd471d95541f3c9
  sess_6761f552b45f4feb8fd9369ca454bd60
)

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

log "WiLoR force rerun starting (${#SESSIONS[@]} sessions) → $LOG"

for SID in "${SESSIONS[@]}"; do
  log ">>> convert $SID"
  t0=$SECONDS
  if (
    cd "$PLATFORM_ROOT" && \
    PYTHONPATH="$PLATFORM_ROOT/src" \
    "$PY" -m ego_platform.cli.convert \
      --station ego-001 \
      --session "$SID" \
      --datalab-root "$DATALAB_ROOT" \
      --corpus-slug ego_001 \
      --mode hands \
      --force
  ) >>"$LOG" 2>&1; then
    log "✅ $SID done in $((SECONDS - t0))s"
  else
    log "❌ $SID failed (exit $?)"
    exit 1
  fi
done

log "All sessions complete"
