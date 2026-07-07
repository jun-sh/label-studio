#!/usr/bin/env bash
# Phase B regression: parquet frame truth + MP4 export (auto/streaming/lerobot gray).
#
# Usage:
#   bash data-lab-platform/scripts/ego-lan-214-phase-b-regression.sh
#   BACKEND=lerobot MAX_EXPORT_SECONDS=900 bash ...   # force backend / timeout
#
# Env:
#   STATION_ID              default ego-lan-214
#   BACKEND                 auto|streaming|lerobot (default auto)
#   EXPECTED_FRAMES         default 6300
#   MAX_EXPORT_SECONDS      fail if export exceeds (default 600)
#   SKIP_EXPORT             1 = parquet/order checks only
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STREAM_HOST="${ROOT}/data-storage/stream/${STATION}"
INGEST_CONTAINER="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
BACKEND="${BACKEND:-auto}"
EXPECTED_FRAMES="${EXPECTED_FRAMES:-6300}"
MAX_EXPORT_SECONDS="${MAX_EXPORT_SECONDS:-600}"
SKIP_EXPORT="${SKIP_EXPORT:-0}"

pass=0
fail=0
warn=0
ok() { echo "[PASS] $*"; pass=$((pass + 1)); }
bad() { echo "[FAIL] $*"; fail=$((fail + 1)); }
note() { echo "[WARN] $*"; warn=$((warn + 1)); }

echo "=== Phase B regression (${STATION}, backend=${BACKEND}) ==="
echo "stream: ${STREAM_HOST}"
echo ""

# --- env / script mount ---
export_backend="$(docker exec "${INGEST_CONTAINER}" printenv DERIVE_VIDEO_EXPORT_BACKEND 2>/dev/null || true)"
if [[ "${export_backend}" == "auto" ]]; then
  ok "DERIVE_VIDEO_EXPORT_BACKEND=auto"
elif [[ "${export_backend}" == "streaming" || "${export_backend}" == "lerobot" ]]; then
  ok "DERIVE_VIDEO_EXPORT_BACKEND=${export_backend} (explicit Phase B)"
elif [[ -z "${export_backend}" || "${export_backend}" == "staging" ]]; then
  note "DERIVE_VIDEO_EXPORT_BACKEND=${export_backend:-<unset>} — recreate stream-ingest/derive-worker for auto default"
else
  bad "DERIVE_VIDEO_EXPORT_BACKEND=${export_backend} (unexpected)"
fi

EXPORT_SCRIPT="/app/scripts/export-videos-from-parquet.py"
if docker exec "${INGEST_CONTAINER}" test -f "${EXPORT_SCRIPT}"; then
  ok "export-videos-from-parquet.py mounted"
else
  bad "export-videos-from-parquet.py missing in ${INGEST_CONTAINER}"
fi

# --- multi-session raw inventory ---
session_count="$(find "${STREAM_HOST}/raw/segments" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"
if [[ "${session_count}" -ge 1 ]]; then
  ok "raw session dirs=${session_count}"
else
  bad "no raw/segments session directories"
fi

for sess_dir in "${STREAM_HOST}"/raw/segments/*/; do
  [[ -d "${sess_dir}" ]] || continue
  sid="$(basename "${sess_dir}")"
  seg_n="$(find "${sess_dir}" -name '*.tar.zst' 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "${seg_n}" -ge 1 ]]; then
    ok "session ${sid}: raw segments=${seg_n}"
  else
    note "session ${sid}: no tar.zst (empty or purged)"
  fi
done

# --- parquet frame order / count ---
if ! python3 - "${STREAM_HOST}" "${EXPECTED_FRAMES}" <<'PY'
import sys
from pathlib import Path
import pyarrow.parquet as pq

root = Path(sys.argv[1])
expected = int(sys.argv[2])
data = root / "data" / "chunk-000" / "file-000.parquet"
if not data.is_file():
    print(f"[FAIL] missing {data}")
    sys.exit(1)
table = pq.read_table(data, columns=["frame_index"])
indices = [int(v) for v in table.column("frame_index").to_pylist()]
if len(indices) != expected:
    print(f"[FAIL] parquet rows={len(indices)} expected={expected}")
    sys.exit(1)
sorted_idx = sorted(indices)
if indices != sorted_idx:
    print("[FAIL] frame_index not stored in ascending order in parquet read")
    sys.exit(1)
for i in range(1, len(sorted_idx)):
    if sorted_idx[i] <= sorted_idx[i - 1]:
        print(f"[FAIL] duplicate or non-monotonic frame_index at {i}")
        sys.exit(1)
span = sorted_idx[-1] - sorted_idx[0] + 1
if span < expected:
    print(f"[FAIL] frame_index span={span} < rows={expected}")
    sys.exit(1)
print(f"[PASS] parquet rows={len(indices)} frame_index [{sorted_idx[0]}..{sorted_idx[-1]}] monotonic")
PY
then
  bad "parquet frame_index validation"
else
  ok "parquet frame_index monotonic and row count"
fi

# --- live session aligns with raw ---
live_sid="$(python3 -c "import json; print(json.load(open('${STREAM_HOST}/live/session.json')).get('sessionId',''))" 2>/dev/null || true)"
if [[ -n "${live_sid}" && -d "${STREAM_HOST}/raw/segments/${live_sid}" ]]; then
  ok "live session ${live_sid} has raw dir"
else
  bad "live session ${live_sid:-<missing>} raw dir mismatch"
fi

if [[ "${SKIP_EXPORT}" == "1" ]]; then
  note "SKIP_EXPORT=1 — skipping MP4 export timing"
else
  container_root="/srv/stream/${STATION}"
  start_ms="$(date +%s%3N)"
  export_json="$(docker exec -e DERIVE_VIDEO_EXPORT_BACKEND="${BACKEND}" "${INGEST_CONTAINER}" \
    timeout "${MAX_EXPORT_SECONDS}" python3 "${EXPORT_SCRIPT}" "${container_root}" --backend "${BACKEND}" 2>&1)" || {
    bad "export failed or timed out (${MAX_EXPORT_SECONDS}s)"
    echo "${export_json}" | tail -5
    export_json=""
  }
  end_ms="$(date +%s%3N)"
  if [[ -n "${export_json}" ]]; then
    elapsed=$((end_ms - start_ms))
    if python3 - "${export_json}" "${EXPECTED_FRAMES}" "${elapsed}" "${MAX_EXPORT_SECONDS}" <<'PY'
import json, sys
raw = sys.argv[1].strip().splitlines()[-1]
d = json.loads(raw)
expected = int(sys.argv[2])
elapsed = int(sys.argv[3])
max_s = int(sys.argv[4])
if not d.get("ok"):
    print(f"[FAIL] export ok=false: {d.get('error', d)}")
    sys.exit(1)
backend = d.get("backend", "?")
frames = d.get("frames") or {}
if elapsed > max_s * 1000:
    print(f"[FAIL] export took {elapsed}ms > {max_s}s budget")
    sys.exit(1)
bad_cams = []
for k, n in frames.items():
    n = int(n)
    if n < expected - 1 or n > expected + 1:
        bad_cams.append(f"{k}={n}")
if bad_cams:
    print("[FAIL] frame count mismatch:", ", ".join(bad_cams))
    sys.exit(1)
print(f"[PASS] export backend={backend} elapsed_ms={elapsed} frames_ok={len(frames)}")
PY
    then
      ok "MP4 export (${BACKEND}) within budget"
    else
      bad "MP4 export validation"
    fi
  fi
fi

# --- disk MP4 vs parquet (post-export or existing) ---
CAMERAS=(
  observation.images.camera_front_left
  observation.images.camera_front_right
  observation.images.camera_rear_left
  observation.images.camera_rear_right
)
for cam in "${CAMERAS[@]}"; do
  mp4="${STREAM_HOST}/videos/${cam}/chunk-000/file-000.mp4"
  if [[ ! -f "${mp4}" ]]; then bad "missing ${cam}"; continue; fi
  frames="$(ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 "${mp4}" 2>/dev/null || echo 0)"
  if [[ "${frames}" -ge $((EXPECTED_FRAMES - 1)) && "${frames}" -le $((EXPECTED_FRAMES + 1)) ]]; then
    ok "disk ${cam} frames=${frames}"
  else
    bad "disk ${cam} frames=${frames} (expected ~${EXPECTED_FRAMES})"
  fi
done

# --- no legacy mux-state required in Phase B ---
mux_state="${STREAM_HOST}/live/mux-state.json"
if [[ -f "${mux_state}" ]]; then
  note "legacy mux-state.json still present (safe to delete after gray)"
else
  ok "no mux-state.json (Phase B clean)"
fi

echo ""
echo "=== summary: pass=${pass} fail=${fail} warn=${warn} ==="
[[ "${fail}" -eq 0 ]]
