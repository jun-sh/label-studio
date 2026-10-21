#!/usr/bin/env bash
# Run on A100: push Isaac Lab /tmp/https asset mirror (~1–2GB) to 3090 for faster replays.
set -euo pipefail
TARGET_HOST="${TARGET_HOST:-10.10.10.51}"
TARGET_USER="${TARGET_USER:-server}"
REMOTE_BASE="${REMOTE_BASE:-/home/server/workspace}"
SRC="${SRC:-/tmp/https}"

SSH_OPTS=(-o StrictHostKeyChecking=no)
if [[ -n "${SSHPASS:-}" ]]; then
  RSYNC_RSH="sshpass -p ${SSHPASS} ssh ${SSH_OPTS[*]}"
else
  RSYNC_RSH="ssh ${SSH_OPTS[*]}"
fi

if [[ ! -d "$SRC" ]]; then
  echo "No local mirror at $SRC — run replay once on A100 first."
  exit 1
fi

echo "==> Rsync $SRC -> ${TARGET_USER}@${TARGET_HOST}:${REMOTE_BASE}/isaac-asset-cache/https/"
rsync -avP -e "$RSYNC_RSH" \
  "$SRC/" "${TARGET_USER}@${TARGET_HOST}:${REMOTE_BASE}/isaac-asset-cache/https/"

echo "==> On 3090, merge into /tmp/https (Isaac Lab default download_dir):"
echo "    mkdir -p /tmp/https && rsync -a ${REMOTE_BASE}/isaac-asset-cache/https/ /tmp/https/"
