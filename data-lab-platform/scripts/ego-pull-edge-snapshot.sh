#!/usr/bin/env bash
# Pull a dated mirror of edge code/config from a capture station into the repo (git-tracked).
# Usage: RC_CAPTURE_PASS=1 bash ego-pull-edge-snapshot.sh server@10.10.11.52 [ego-002]
set -euo pipefail

TARGET="${1:?server@host}"
STATION="${2:-ego-002}"
STAMP="$(date +%Y%m%d)"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${ROOT}/edge-station-snapshots/${STATION}-${STAMP}"

SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=20)
export SSHPASS="${SSHPASS:-${RC_CAPTURE_PASS:-1}}"
RSYNC_RSH="sshpass -e ssh ${SSH_OPTS[*]}"

mkdir -p "$DEST"
echo "==> Snapshot ${TARGET} -> ${DEST}"

rsync -av --delete -e "$RSYNC_RSH" "${TARGET}:/home/server/ego-web/" "${DEST}/ego-web/"
rsync -av --delete -e "$RSYNC_RSH" "${TARGET}:/home/server/ego-web-staging/" "${DEST}/ego-web-staging/"
mkdir -p "${DEST}/ego-studio-src"
rsync -av --delete -e "$RSYNC_RSH" \
  "${TARGET}:/home/server/workspace/ego-studio/src/ego_capture_studio/" \
  "${DEST}/ego-studio-src/ego_capture_studio/"
rsync -av -e "$RSYNC_RSH" \
  "${TARGET}:/home/server/workspace/ego-studio/pyproject.toml" \
  "${DEST}/ego-studio-src/" 2>/dev/null || true
rsync -av --delete -e "$RSYNC_RSH" \
  "${TARGET}:/home/server/.config/systemd/user/" \
  "${DEST}/systemd-user/"
rsync -av --delete -e "$RSYNC_RSH" \
  "${TARGET}:/home/server/.config/ego-station.env.d/" \
  "${DEST}/ego-station.env.d/"

find "$DEST" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
find "$DEST" -name '*.pyc' -delete 2>/dev/null || true

sshpass -e ssh "${SSH_OPTS[@]}" "$TARGET" \
  'uname -r; hostname; (cd ~/workspace/ego-studio && git rev-parse HEAD 2>/dev/null) || echo no-git' \
  > "${DEST}/STATION_META.txt"

cp "$(dirname "$0")/../edge-station-snapshots/ego-002-20261011/README.md" "${DEST}/README.md" 2>/dev/null || cat > "${DEST}/README.md" <<EOF
# ${STATION} edge snapshot from ${TARGET} (${STAMP})
See data-lab-platform/scripts/ego-pull-edge-snapshot.sh
EOF

echo "==> Done: ${DEST} ($(du -sh "$DEST" | awk '{print $1}'))"
echo "Optional merge into SSOT: RC_CAPTURE_PASS=1 bash scripts/ego-130-pull-from-station.sh ${TARGET}"
