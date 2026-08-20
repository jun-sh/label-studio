#!/usr/bin/env bash
# Re-derive all raw sessions sequentially (SINGLE_EPISODE=0 grayscale path).
# Wipes derived artifacts, bootstraps DERIVE_PENDING, derives each session until READY.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
STREAM="${ROOT}/data-storage/stream/${STATION}"
LOG="${ROOT}/data-lab-platform/.full-rederive-all.log"

log() { echo "[full-rederive] $*" | tee -a "${LOG}"; }
die() { echo "[full-rederive] ERROR: $*" >&2 | tee -a "${LOG}"; exit 1; }

: > "${LOG}"
log "station=${STATION}"

docker stop data-lab-derive-worker-1 >/dev/null 2>&1 || true
# shellcheck source=ego-reset-34-wipe-stream.sh
source "${ROOT}/data-lab-platform/scripts/ego-reset-34-wipe-stream.sh"
ego_reset_wipe_stream_derived "${STREAM}" "${STATION}"
bash "${ROOT}/data-lab-platform/scripts/ego-bootstrap-raw-sessions.sh"

mapfile -t SESSIONS < <(ls -1 "${STREAM}/raw/segments" | sort)
log "sessions=${#SESSIONS[@]}"

for sid in "${SESSIONS[@]}"; do
  log "derive ${sid}"
  if ! STATION_ID="${STATION}" bash "${ROOT}/data-lab-platform/scripts/ego-derive" run --station "${STATION}" --session "${sid}" --json >>"${LOG}" 2>&1; then
    die "derive failed for ${sid}"
  fi
  [[ -f "${STREAM}/state/sessions/${sid}/session.READY" ]] || die "session.READY missing for ${sid}"
done

python3 - "${STREAM}" <<'PY'
import json, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
ep = json.loads((root / "meta/episodes/episode_000.json").read_text())
jsonl = sum(1 for _ in open(root / "data/chunk-000/file-000.jsonl"))
mp4 = root / "videos/observation.images.camera_front_left/chunk-000/file-000.mp4"
r = subprocess.run(
    ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
     "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", str(mp4)],
    capture_output=True, text=True,
)
mp4_frames = int(r.stdout.strip() or "0")
length = int(ep.get("length") or 0)
ready = len(list((root / "state/sessions").glob("*/session.READY")))
print(json.dumps({
    "ready": ready,
    "frame_map_length": length,
    "jsonl_rows": jsonl,
    "mp4_frames": mp4_frames,
    "aligned": length == jsonl == mp4_frames,
}))
if not (length == jsonl == mp4_frames):
    sys.exit(1)
PY

docker start data-lab-derive-worker-1 >/dev/null 2>&1 || true
log "done: all sessions READY and aligned"
