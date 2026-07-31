#!/usr/bin/env bash
# Rebuild LeRobot derive outputs from raw/ tar.zst (keeps raw segments untouched).
# Use when episodes-index and data.parquet are out of sync after a partial derive.
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STREAM="${ROOT}/data-storage/stream/${STATION}"
INGEST="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
DERIVE="${ROOT}/data-lab-platform/scripts/ego-derive"

log() { echo "[rebuild-lerobot] $*"; }
die() { echo "[rebuild-lerobot] ERROR: $*" >&2; exit 2; }

[[ -d "${STREAM}/raw/segments" ]] || die "missing ${STREAM}/raw/segments"

log "clearing derived LeRobot artifacts (keeping raw/)…"
rm -rf "${STREAM}/archive"
rm -rf "${STREAM}/data" "${STREAM}/videos"
rm -rf "${STREAM}/meta/episodes" "${STREAM}/meta/stats"
rm -f "${STREAM}/meta/tasks.jsonl" "${STREAM}/meta/tasks.parquet"
rm -f "${STREAM}/live/episodes-index.json"
rm -rf "${STREAM}/live/derive/markers"
rm -rf "${STREAM}/live/derive/pending_episode"
rm -rf "${STREAM}/live/sessions"
rm -f "${STREAM}/live/parquet_sync.json"

python3 - "${STREAM}/meta/info.json" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.is_file():
    sys.exit(0)
info = json.loads(p.read_text(encoding="utf-8"))
for k in ("total_frames", "total_episodes", "ingest_row_count", "frame_index_min", "frame_index_max"):
    info.pop(k, None)
info["total_frames"] = 0
info["total_episodes"] = 0
info["splits"] = {"train": "0:0"}
p.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
PY

mapfile -t SESSIONS < <(
  find "${STREAM}/raw/segments" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort
)
[[ ${#SESSIONS[@]} -gt 0 ]] || die "no sessions under raw/segments"

log "re-deriving ${#SESSIONS[@]} session(s) via official LeRobot path…"
for sid in "${SESSIONS[@]}"; do
  log "  → ${sid}"
  DERIVE_PARQUET_BACKEND=lerobot DERIVE_VIDEO_EXPORT_BACKEND=lerobot \
    bash "${DERIVE}" run --station "${STATION}" --session "${sid}" || die "derive failed for ${sid}"
done

log "meta sync from episodes-index…"
docker exec "${INGEST}" python3 /app/scripts/sync-stream-parquet.py --meta-only "/srv/stream/${STATION}"

log "done — run: bash data-lab-platform/scripts/ego-lan-214-multi-session-regression.sh"
