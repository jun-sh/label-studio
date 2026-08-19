#!/usr/bin/env bash
# Phase 6 acceptance: ops reset scripts preserve raw/segments; sensor_raw IMU path; Phase5 regression.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STUDIO="${ROOT}/lerobot-studio"
SCRIPTS="${ROOT}/scripts"
PLATFORM_ROOT="${EGO_PLATFORM_ROOT:-$(dirname "$(dirname "$ROOT")")/ego-platform}"

log() { echo "[rc-ego-001-phase6] $*"; }

log "=== static: reset scripts Phase0 §9.3 ==="
test -x "${SCRIPTS}/ego-reset-34-wipe-stream.sh"
test -x "${SCRIPTS}/ego-reset-34-only.sh"
test -x "${SCRIPTS}/ego-001-reset-for-rerun.sh"
rg -q 'ego_reset_wipe_stream_derived' "${SCRIPTS}/ego-reset-34-wipe-stream.sh"
rg -q 'raw/segments' "${SCRIPTS}/ego-reset-34-only.sh"
rg -q 'ego-reset-34-only.sh' "${SCRIPTS}/ego-001-reset-for-rerun.sh"
! rg -q 'rm -rf "\$\{STREAM_HOST\}"' "${SCRIPTS}/ego-reset-34-only.sh"
! rg -q 'rm -rf "\$\{STREAM_HOST\}"' "${SCRIPTS}/ego-001-reset-for-rerun.sh"
rg -q 'v0.0.13' "${SCRIPTS}/ego-reset-34-only.sh"
rg -q 'sensor_raw/imu' "${SCRIPTS}/ego-run-pipeline"

log "=== static: ego-platform sensor_raw IMU merge ==="
test -f "${PLATFORM_ROOT}/src/ego_platform/lerobot/sensor_raw_imu.py"
rg -q 'SENSOR_RAW_IMU_REL' "${PLATFORM_ROOT}/src/ego_platform/lerobot/sensor_raw_imu.py"
rg -q 'merge_sensor_raw_imu_from_stream' "${PLATFORM_ROOT}/src/ego_platform/lerobot/sensor_raw_imu.py"
rg -q 'merge_sensor_raw_imu_from_stream' "${PLATFORM_ROOT}/src/ego_platform/lerobot/append.py"
! rg -q 'data/chunk-000/high_freq/imu_200hz.parquet' "${PLATFORM_ROOT}/src/ego_platform/lerobot/sensor_raw_imu.py"

log "=== wipe helper: preserve raw/segments, purge derived ==="
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ego-phase6-wipe.XXXXXX")"
STATION_ROOT="${TMP}/ego-001"
mkdir -p "${STATION_ROOT}/raw/segments"
mkdir -p "${STATION_ROOT}/data/chunk-000" \
  "${STATION_ROOT}/videos/chunk-000" \
  "${STATION_ROOT}/sensor_raw/imu/chunk-000" \
  "${STATION_ROOT}/staging/cam" \
  "${STATION_ROOT}/live/derive" \
  "${STATION_ROOT}/state/sessions/s1" \
  "${STATION_ROOT}/meta/episodes"
echo '{"sensor_raw":{}}' > "${STATION_ROOT}/meta/info.json"
echo 'fake' > "${STATION_ROOT}/raw/segments/seg_000001.tar.zst"
echo 'fake' > "${STATION_ROOT}/data/chunk-000/file-000.parquet"

# shellcheck source=ego-reset-34-wipe-stream.sh
source "${SCRIPTS}/ego-reset-34-wipe-stream.sh"
ego_reset_wipe_stream_derived "${STATION_ROOT}" "ego-001"

test -f "${STATION_ROOT}/raw/segments/seg_000001.tar.zst"
test ! -d "${STATION_ROOT}/data"
test ! -d "${STATION_ROOT}/videos"
test ! -d "${STATION_ROOT}/sensor_raw"
test ! -d "${STATION_ROOT}/staging"
test ! -d "${STATION_ROOT}/live"
test ! -d "${STATION_ROOT}/state/sessions"
test ! -f "${STATION_ROOT}/meta/info.json"
rm -rf "${TMP}"

log "=== derive from raw: IMU ingest smoke ==="
cd "${STUDIO}"
node --test derive/derive.test.mjs

log "=== ego-platform merge smoke ==="
PYTHONPATH="${PLATFORM_ROOT}/src" python3 - <<'PY'
from pathlib import Path
import json
import pyarrow as pa
import pyarrow.parquet as pq
import tempfile
import os

from ego_platform.lerobot.sensor_raw_imu import merge_sensor_raw_imu_from_stream, SENSOR_RAW_IMU_REL

tmp = Path(tempfile.mkdtemp())
stream = tmp / "stream"
corpus = tmp / "corpus"
rel = SENSOR_RAW_IMU_REL
(stream / rel.parent).mkdir(parents=True)
(corpus / "meta").mkdir(parents=True)
table = pa.table({"timestamp_ns": [1, 2], "session_id": ["s1", "s1"]})
pq.write_table(table, stream / rel)
(stream / "meta").mkdir(parents=True)
(stream / "meta/info.json").write_text(json.dumps({
    "sensor_raw": {"imu": {"path": str(rel), "rate_hz": 200}}
}))
(corpus / "meta/info.json").write_text(json.dumps({"total_frames": 0}))

out = merge_sensor_raw_imu_from_stream(corpus, stream, session_id="s1")
assert out["ok"] and out["rows"] == 2
assert (corpus / rel).is_file()
info = json.loads((corpus / "meta/info.json").read_text())
assert info["sensor_raw"]["imu"]["corpus_rows"] == 2
print("merge_sensor_raw_imu_from_stream ok")
PY

log "=== Phase 5 regression ==="
"${SCRIPTS}/rc-ego-001-phase5.sh"

log "Phase 6 acceptance PASSED"
log "Deploy 34: data-lab-platform/scripts/deploy-stream-ingest-v0.0.13-rc.6.sh"
