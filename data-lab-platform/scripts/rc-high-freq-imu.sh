#!/usr/bin/env bash
# RC-5: LeRobot v3 high_freq IMU parquet + vendor_meta (Phase B).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${RC_STATION:-ego-001}"
STREAM_ROOT="${STREAM_ROOT:-${ROOT}/data-storage/stream}"
STATION_ROOT="${STREAM_ROOT}/${STATION}"
HF_REL="data/chunk-000/high_freq/imu_200hz.parquet"
HF_PATH="${STATION_ROOT}/${HF_REL}"
INFO_PATH="${STATION_ROOT}/meta/info.json"
MIN_ROWS="${RC_HIGH_FREQ_MIN_ROWS:-1000}"

fail() { echo "RC-5 FAIL: $*" >&2; exit 1; }

echo "=== RC-5 high_freq IMU (${STATION}) ==="

[[ -f "${HF_PATH}" ]] || fail "missing ${HF_REL}"

python3 - <<PY
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

root = Path("${STATION_ROOT}")
hf = root / "${HF_REL}"
info_path = root / "meta/info.json"

if not info_path.is_file():
    print("RC-5 FAIL: missing meta/info.json", file=sys.stderr)
    sys.exit(1)

info = json.loads(info_path.read_text(encoding="utf-8"))
vm = info.get("vendor_meta") or {}
hf_meta = (vm.get("high_freq") or {}).get("imu_200hz") or {}
if not hf_meta:
    print("RC-5 FAIL: vendor_meta.high_freq.imu_200hz missing", file=sys.stderr)
    sys.exit(1)

rows = pq.read_metadata(hf).num_rows
min_rows = int("${MIN_ROWS}")
if rows < min_rows:
    print(f"RC-5 FAIL: imu parquet rows={rows} < {min_rows}", file=sys.stderr)
    sys.exit(1)

main_rows = sum(
    pq.read_metadata(p).num_rows
    for p in sorted(root.glob("data/**/*.parquet"))
    if "high_freq" not in p.parts
)
total_frames = int(info.get("total_frames") or 0)
if total_frames > 0 and total_frames != main_rows:
    print(
        f"RC-5 FAIL: total_frames={total_frames} != main parquet rows={main_rows}",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"RC-5 ok rows={rows} main_frames={main_rows} vendor_path={hf_meta.get('path')}")
PY

echo "=== RC-5 PASSED ==="
