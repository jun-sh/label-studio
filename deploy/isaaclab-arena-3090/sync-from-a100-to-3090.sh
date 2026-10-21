#!/usr/bin/env bash
# Run on A100 (10.10.10.34) to push Arena install to 3090 desktop.
set -euo pipefail
TARGET_HOST="${TARGET_HOST:-10.10.10.51}"
TARGET_USER="${TARGET_USER:-server}"
SRC="${SRC:-/home/user01/workspace/isaaclab-arena-run}"
REMOTE_BASE="${REMOTE_BASE:-/home/server/workspace}"
DEPLOY_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SSH_OPTS=(-o StrictHostKeyChecking=no)
if [[ -n "${SSHPASS:-}" ]]; then
  RSYNC_RSH="sshpass -p ${SSHPASS} ssh ${SSH_OPTS[*]}"
else
  RSYNC_RSH="ssh ${SSH_OPTS[*]}"
fi

echo "==> Target: ${TARGET_USER}@${TARGET_HOST}:${REMOTE_BASE}"
echo "==> Rsync Arena tree (large; may take 30-90 min)..."
rsync -avP --delete-delay -e "$RSYNC_RSH" \
  --exclude '.venv/' \
  --exclude 'IsaacLab-Arena/.venv/' \
  --exclude 'IsaacLab-Arena/.git/' \
  --exclude 'arena-release-*.zip' \
  --exclude 'isaaclab.zip' \
  "$SRC/" "${TARGET_USER}@${TARGET_HOST}:${REMOTE_BASE}/isaaclab-arena-run/"

echo "==> Deploy helper scripts..."
rsync -avP -e "$RSYNC_RSH" \
  "$DEPLOY_SRC/" "${TARGET_USER}@${TARGET_HOST}:${REMOTE_BASE}/isaaclab-arena-3090/"

echo "==> Remote run-replay-gui wrapper..."
sshpass -p "${SSHPASS:-1}" ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" bash -s <<'REMOTE'
set -euo pipefail
chmod +x /home/server/workspace/isaaclab-arena-3090/*.sh
cat > /home/server/workspace/run-replay-gui.sh <<'EOF'
#!/usr/bin/env bash
exec /home/server/workspace/isaaclab-arena-3090/run-replay-gui.sh
EOF
chmod +x /home/server/workspace/run-replay-gui.sh
if [[ -d /home/server/workspace/isaaclab-arena-run/IsaacLab-Arena/.venv ]]; then
  /home/server/workspace/isaaclab-arena-3090/fix-venv-paths-3090.sh
fi
REMOTE
echo "Done. Starting uv sync + replay on 3090 (background)..."
sshpass -p "${SSHPASS:-1}" ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" \
  'nohup /home/server/workspace/isaaclab-arena-3090/post-sync-setup-and-replay-3090.sh \
    > /home/server/workspace/post-sync-3090.log 2>&1 & echo post_sync_pid=$!'
