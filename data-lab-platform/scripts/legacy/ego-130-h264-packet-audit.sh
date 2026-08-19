#!/usr/bin/env bash
# Compare jsonl row count vs per-camera MP4 packet counts (segment_mp4 path).
# Usage:
#   bash data-lab-platform/scripts/ego-130-h264-packet-audit.sh [station]
#   RC_STATION=ego-001 bash data-lab-platform/scripts/ego-130-h264-packet-audit.sh
set -euo pipefail

STATION="${1:-${RC_STATION:-ego-001}}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STREAM="${ROOT}/data-storage/stream/${STATION}"
JSONL="${STREAM}/data/chunk-000/file-000.jsonl"

[[ -f "${JSONL}" ]] || { echo "FAIL: missing ${JSONL}"; exit 1; }

ROWS="$(wc -l < "${JSONL}")"
TARGET=$(( ROWS * 85 / 100 ))
echo "station=${STATION} jsonl_rows=${ROWS} gate_min_85pct=${TARGET}"

python3 - <<PY
import json, subprocess, sys
from pathlib import Path

stream = Path("${STREAM}")
rows = int("${ROWS}")
target = int("${TARGET}")

info = json.loads((stream / "meta/info.json").read_text())
keys = [k for k in info.get("features", {}) if k.startswith("observation.images.")]
if not keys:
    print("FAIL: no video keys in info.json", file=sys.stderr)
    sys.exit(1)

counts = {}
for key in sorted(keys):
    mp4 = stream / "videos" / key / "chunk-000" / "file-000.mp4"
    if not mp4.is_file():
        print(f"FAIL: missing {mp4}", file=sys.stderr)
        sys.exit(1)
    n = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(mp4)],
        text=True,
    ).strip()
    counts[key] = int(n)

min_f, max_f = min(counts.values()), max(counts.values())
spread = max_f - min_f
ok_gate = min_f >= target and spread <= 120
short = min(k.split(".")[-1] for k, v in counts.items() if v == min_f)

print("camera_packets:")
for k, v in counts.items():
    pct = round(100 * v / rows, 1) if rows else 0
    flag = " <-- lowest" if v == min_f else ""
    print(f"  {k.split('.')[-1]}: {v} ({pct}% of jsonl){flag}")
print(f"spread={spread} min_vs_gate={min_f}>={target} cross_cam_ok={spread <= 120}")
if not ok_gate:
    print(f"WARN: below v0.0.9.2 READY gate (min {min_f} < {target} or spread {spread} > 120)", file=sys.stderr)
    print(f"hint: investigate 130 encoder for '{short}' (GOP/keyframe alignment)", file=sys.stderr)
    sys.exit(1)
print("OK: packet counts within segment_mp4 readiness gate")
PY
