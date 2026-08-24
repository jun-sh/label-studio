#!/usr/bin/env bash
# Phase B: migrate ego-001 legacy L2 → P2 unit layout (derived/ + manifest + multi-episode L2).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
STREAM="${ROOT}/data-storage/stream/${STATION}"
LOG="${ROOT}/data-lab-platform/.p2-unit-migrate.log"
SUFFIX="bak-p2-$(date +%Y%m%d)"
LEGACY_FRAMES="${LEGACY_FRAMES:-4307}"

log() { echo "[p2-migrate] $*" | tee -a "${LOG}"; }
die() { echo "[p2-migrate] ERROR: $*" >&2 | tee -a "${LOG}"; exit 1; }

: > "${LOG}"
log "station=${STATION} legacy_frames=${LEGACY_FRAMES}"

docker stop data-lab-derive-worker-1 >/dev/null 2>&1 || true

# Order sessions by DONE_UPLOAD timestamp (upload sequence).
mapfile -t SESSIONS < <(python3 - "${STREAM}" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sessions = []
for sess_dir in sorted((root / "state/sessions").glob("sess_*")):
    sid = sess_dir.name
    marker = sess_dir / "session.DONE_UPLOAD"
    if not marker.is_file():
        continue
    try:
        data = json.loads(marker.read_text())
        at = data.get("at") or data.get("uploadedAt") or sid
    except Exception:
        at = sid
    sessions.append((at, sid))
for _, sid in sorted(sessions):
    print(sid)
PY
)
log "sessions=${#SESSIONS[@]}"

# Step 1: derive immutable units (L1) — legacy L2 untouched until rebuild-view.
rm -rf "${STREAM}/derived" "${STREAM}/manifest"
mkdir -p "${STREAM}/derived" "${STREAM}/manifest"

for sid in "${SESSIONS[@]}"; do
  log "unit-derive ${sid}"
  if ! DERIVE_LAYOUT=unit STATION_ID="${STATION}" bash "${ROOT}/data-lab-platform/scripts/ego-derive" run \
    --station "${STATION}" --session "${sid}" --json >>"${LOG}" 2>&1; then
    die "unit derive failed for ${sid}"
  fi
  [[ -f "${STREAM}/derived/${sid}/unit.json" ]] || die "unit.json missing for ${sid}"
done

# Step 2: backup legacy L2 before rebuild.
for rel in data videos; do
  if [[ -d "${STREAM}/${rel}" && ! -e "${STREAM}/${rel}.${SUFFIX}" ]]; then
    log "backup ${rel} -> ${rel}.${SUFFIX}"
    mv "${STREAM}/${rel}" "${STREAM}/${rel}.${SUFFIX}"
  fi
done
if [[ -d "${STREAM}/meta" && ! -e "${STREAM}/meta.${SUFFIX}" ]]; then
  log "backup meta -> meta.${SUFFIX}"
  cp -a "${STREAM}/meta" "${STREAM}/meta.${SUFFIX}"
fi

mkdir -p "${STREAM}/data" "${STREAM}/videos" "${STREAM}/meta"

# Step 3: publish multi-episode L2 from units.
log "rebuild-view"
if ! DERIVE_LAYOUT=unit STATION_ID="${STATION}" bash "${ROOT}/data-lab-platform/scripts/ego-derive" rebuild-view --station "${STATION}" --json >>"${LOG}" 2>&1; then
  die "rebuild-view failed"
fi

log "fsck"
if ! DERIVE_LAYOUT=unit STATION_ID="${STATION}" bash "${ROOT}/data-lab-platform/scripts/ego-derive" fsck --station "${STATION}" --json >>"${LOG}" 2>&1; then
  die "fsck failed"
fi

python3 - "${STREAM}" "${LEGACY_FRAMES}" <<'PY'
import json, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
legacy = int(sys.argv[2])
manifest = json.loads((root / "manifest/manifest.json").read_text())
info = json.loads((root / "meta/info.json").read_text())
units = len(list((root / "derived").glob("sess_*/unit.json")))
parquet = int(subprocess.check_output([
    "python3", "-c",
    "import sys; import pyarrow.parquet as pq; from pathlib import Path; r=Path(sys.argv[1]); print(sum(pq.read_metadata(p).num_rows for p in sorted(r.glob('data/**/*.parquet'))))",
    str(root),
], text=True).strip())
mp4 = int(subprocess.check_output([
    "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
    "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1",
    str(root / "videos/observation.images.camera_front_left/chunk-000/file-000.mp4"),
], text=True).strip() or "0")
report = {
    "units": units,
    "episodes": len(manifest.get("episodes", [])),
    "manifest_frames": manifest.get("total_frames"),
    "info_total_frames": info.get("total_frames"),
    "info_total_episodes": info.get("total_episodes"),
    "parquet_rows": parquet,
    "legacy_frames": legacy,
    "aligned": manifest.get("total_frames") == parquet == legacy,
}
print(json.dumps(report, indent=2))
if not report["aligned"] or units != len(manifest.get("episodes", [])):
    sys.exit(1)
PY

docker start data-lab-derive-worker-1 >/dev/null 2>&1 || true
log "Phase B migration complete. Rollback: restore data/videos/meta from *.${SUFFIX}"
