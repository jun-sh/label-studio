#!/usr/bin/env bash
set -euo pipefail
source /opt/datalab/env/stream-storage.env
LOG=/opt/datalab/log/purge-cold-archive.log
mkdir -p "$(dirname "$LOG")" "$COLD_ROOT"
exec >>"$LOG" 2>&1
echo "=== $(date -Is) purge-cold start ==="
find "$COLD_ROOT" -maxdepth 1 -type f -name '*.tar.gz' -mtime +"${COLD_RETENTION_DAYS}" -print -delete
echo "=== $(date -Is) purge-cold done ==="
