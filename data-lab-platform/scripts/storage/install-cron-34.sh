#!/usr/bin/env bash
# Install 34 host cron for hot-tier + cold archive (run once as root on 10.10.10.34).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV_DST=/opt/datalab/env/stream-storage.env
LOG_DIR=/opt/datalab/log

sudo mkdir -p /opt/datalab/env "$LOG_DIR"
if [ ! -f "$ENV_DST" ]; then
  sudo cp "$REPO_ROOT/data-lab-platform/scripts/storage/stream-storage.env.example" "$ENV_DST"
  echo "Edit $ENV_DST (STATION_ROOT volume path) then re-run."
fi

sudo chmod +x "$REPO_ROOT/data-lab-platform/scripts/storage/"*.sh
sudo bash "$REPO_ROOT/data-lab-platform/scripts/storage/setup-ego-archive.sh"

CRON_FILE=/etc/cron.d/datalab-stream-storage
sudo tee "$CRON_FILE" >/dev/null <<EOF
# Data Lab stream hot/cold tier (session-level migration only)
*/5 * * * * root STREAM_STORAGE_ENV=$ENV_DST $REPO_ROOT/data-lab-platform/scripts/storage/hot-tier-enforce.sh
15 */6 * * * root STREAM_STORAGE_ENV=$ENV_DST $REPO_ROOT/data-lab-platform/scripts/storage/archive-to-cold.sh
EOF
echo "Installed $CRON_FILE"
