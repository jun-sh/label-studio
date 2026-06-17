#!/usr/bin/env bash
# Start SAM 2.1 interactive ML backend for Data Lab / Label Studio.
# Must run from repo root or any path; uses ml-services/.env.sam
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ML_ROOT="$SCRIPT_DIR"
SAM2_DIR="$ML_ROOT/sam2"
BACKEND_DIR="$ML_ROOT/label-studio-ml-backend/label_studio_ml/examples/segment_anything_2_image"
ENV_FILE="$ML_ROOT/.env.sam"
PID_FILE="$ML_ROOT/sam-backend.pid"
LOG_FILE="$ML_ROOT/sam-backend.log"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE — copy from .env.sam.example and set LABEL_STUDIO_API_KEY" >&2
  exit 1
fi

if [[ ! -d "$SAM2_DIR" || ! -d "$BACKEND_DIR" ]]; then
  echo "Missing sam2 or label-studio-ml-backend under $ML_ROOT" >&2
  exit 1
fi

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "SAM backend already running (pid $(cat "$PID_FILE"))"
  exit 0
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

CKPT="$SAM2_DIR/checkpoints/${MODEL_CHECKPOINT:-sam2.1_hiera_large.pt}"
if [[ ! -f "$CKPT" ]]; then
  echo "Checkpoint not found: $CKPT" >&2
  echo "Download: curl -L -o $CKPT https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt" >&2
  exit 1
fi

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

cd "$SAM2_DIR"

if [[ "${SAM_FOREGROUND:-}" == "1" ]]; then
  echo "Starting SAM ML backend (foreground) on :${PORT:-9090} (cwd=$SAM2_DIR)"
  exec python3 "$BACKEND_DIR/_wsgi.py" \
    --host 0.0.0.0 \
    -p "${PORT:-9090}"
fi

echo "Starting SAM ML backend on :${PORT:-9090} (cwd=$SAM2_DIR)"
nohup python3 "$BACKEND_DIR/_wsgi.py" \
  --host 0.0.0.0 \
  -p "${PORT:-9090}" \
  >>"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"
sleep 2

if curl -fsS "http://127.0.0.1:${PORT:-9090}/" >/dev/null 2>&1; then
  echo "SAM backend is UP at http://127.0.0.1:${PORT:-9090}/"
  echo "Connect in Label Studio: http://${LABEL_STUDIO_URL#http://} → Settings → ML → http://10.10.10.34:${PORT:-9090}"
else
  echo "Backend started (pid $(cat "$PID_FILE")) but health check pending — see $LOG_FILE"
fi
