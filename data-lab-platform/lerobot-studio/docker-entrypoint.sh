#!/bin/sh
set -eu

ROOT="${LEROBOT_STUDIO_ROOT:-/srv/lerobot}"
export LEROBOT_STUDIO_ROOT="$ROOT"

if [ ! -f "${ROOT}/assets/index-BM8rEaYC.js" ]; then
  echo "==> First start: downloading IO-AI LeRobot Studio static assets into ${ROOT}"
  /app/download-assets.sh
else
  echo "==> LeRobot Studio assets present at ${ROOT}"
fi

exec node /app/server.mjs
