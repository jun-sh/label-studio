#!/usr/bin/env bash
# Repeated single-session derive stability (preserve raw tar.zst, re-derive to session.READY).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
SESSION="${STABILITY_SESSION:-sess_776836bf9af746c9b1f1a0ef9d9bf1ff}"
SEGMENT="${STABILITY_SEGMENT:-seg_000001}"
FRAMES="${STABILITY_FRAMES:-304}"
RUNS="${STABILITY_RUNS:-5}"
STREAM_HOST="${ROOT}/data-storage/stream/${STATION}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# shellcheck source=ego-reset-34-wipe-stream.sh
source "${SCRIPT_DIR}/ego-reset-34-wipe-stream.sh"

log() { echo "[stability] $*"; }
fail() { echo "[stability] FAIL: $*" >&2; exit 1; }

bootstrap_session() {
  docker stop data-lab-derive-worker-1 >/dev/null 2>&1 || true
  docker exec data-lab-stream-ingest-1 node -e "
import fs from 'node:fs';
import path from 'node:path';
import { bootstrapDatasetSchema } from '/app/lerobot-converter.mjs';
import { videoKeysForStation } from '/app/ingest/staging-materialize.mjs';
import { transitionSegmentState, SEGMENT_INGEST_STATUS } from '/app/ingest/segment-state.mjs';
import { markSessionUploadDone } from '/app/session-markers.mjs';
import { ensureDir, writeJsonAtomic } from '/app/ingest/io.mjs';

const stationId = '${STATION}';
const root = '/srv/stream/' + stationId;
const sessionId = '${SESSION}';
const segmentId = '${SEGMENT}';

const features = {};
for (const key of videoKeysForStation(stationId)) {
  features[key] = {
    dtype: 'video',
    shape: [1200, 1920, 3],
    names: ['height', 'width', 'channels'],
    info: {
      'video.height': 1200,
      'video.width': 1920,
      'video.codec': 'h264',
      'video.pix_fmt': 'yuv420p',
      'video.is_depth_map': false,
      'video.fps': 30,
      'video.channels': 3,
      has_audio: false,
    },
  };
}
features['observation.state'] = { dtype: 'float32', shape: [6], names: ['gx','gy','gz','ax','ay','az'] };
features['observation.pose'] = { dtype: 'float32', shape: [7], names: ['x','y','z','qx','qy','qz','qw'] };
features['observation.hands'] = { dtype: 'float32', shape: [63], names: null };
features.action = { dtype: 'float32', shape: [1], names: null };
features['observation.imu_accel'] = { dtype: 'float32', shape: [3], names: ['x','y','z'] };
features['observation.imu_gyro'] = { dtype: 'float32', shape: [3], names: ['x','y','z'] };
features['observation.imu_timestamp'] = { dtype: 'float64', shape: [1], names: null };

const info = bootstrapDatasetSchema({
  codebase_version: 'v3.0',
  robot_type: 'oak_4p_ego',
  total_episodes: 1,
  total_frames: 0,
  total_tasks: 1,
  chunks_size: 1000,
  fps: 30,
  splits: { train: '0:1' },
  data_path: 'data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet',
  video_path: 'videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4',
  features,
}, null);
ensureDir(path.join(root, 'meta'));
writeJsonAtomic(path.join(root, 'meta/info.json'), info);

transitionSegmentState(root, sessionId, segmentId, SEGMENT_INGEST_STATUS.DERIVE_PENDING, {
  frame_count: ${FRAMES},
  integrity: { ok: true },
});
markSessionUploadDone(root, sessionId, { stationId, source: 'stability-bootstrap' });
" >/dev/null
}

collect_metrics() {
  local run="$1"
  python3 - "${STREAM_HOST}" "${SESSION}" "${run}" <<'PY'
import json, sys
from pathlib import Path
try:
    import pyarrow.parquet as pq
except ImportError:
    pq = None

root = Path(sys.argv[1])
session = sys.argv[2]
run = int(sys.argv[3])

def g6_null_ratio(jsonl_path: Path) -> float:
    missing = total = 0
    for line in jsonl_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        for key in ("observation.imu_accel", "observation.imu_gyro"):
            total += 1
            val = row.get(key)
            if val is None or (isinstance(val, list) and any(v is None for v in val)):
                missing += 1
        ts = row.get("observation.imu_timestamp")
        if ts is None:
            missing += 1
    return 100.0 * missing / max(1, total)

jsonl = root / "data/chunk-000/file-000.jsonl"
imu_pq = root / "sensor_raw/imu/chunk-000/file-000.parquet"
ready = root / "state/sessions" / session / "session.READY"
mux = root / "live/derive/mux_validated.json"
mp4s = list((root / "videos").glob("**/*.mp4")) if (root / "videos").exists() else []

sensor_rows = both_ok = 0
if pq and imu_pq.is_file():
    table = pq.read_table(imu_pq)
    sensor_rows = table.num_rows
    accels = table.column("accel").to_pylist()
    gyros = table.column("gyro").to_pylist()
    both_ok = sum(1 for a, g in zip(accels, gyros) if a is not None and g is not None)

main_rows = sum(1 for l in jsonl.read_text().splitlines() if l.strip()) if jsonl.is_file() else 0
mux_ok = json.loads(mux.read_text()).get("ok") if mux.is_file() else None

print(json.dumps({
    "run": run,
    "main_rows": main_rows,
    "sensor_raw_rows": sensor_rows,
    "sensor_raw_both_ok_pct": round(100.0 * both_ok / max(1, sensor_rows), 2),
    "g6_null_pct": round(g6_null_ratio(jsonl), 4) if jsonl.is_file() else None,
    "session_ready": ready.is_file(),
    "mux_validated_ok": mux_ok,
    "mp4_count": len(mp4s),
}))
PY
}

[[ -f "${STREAM_HOST}/raw/segments/${SESSION}/${SEGMENT}.tar.zst" ]] \
  || fail "raw archive missing: ${SESSION}/${SEGMENT}.tar.zst"

log "station=${STATION} session=${SESSION} runs=${RUNS} image=v0.0.13-fix2"
RESULTS=()

for ((i = 1; i <= RUNS; i++)); do
  log "=== run ${i}/${RUNS}: wipe derived (preserve raw) ==="
  ego_reset_wipe_stream_derived "${STREAM_HOST}" "${STATION}"
  bootstrap_session

  log "=== run ${i}/${RUNS}: derive ==="
  t0=$(date +%s%3N)
  derive_json="${ROOT}/data-lab-platform/.stability-derive-${i}.json"
  derive_raw="${ROOT}/data-lab-platform/.stability-derive-${i}.raw"
  if ! STATION_ID="${STATION}" bash "${ROOT}/data-lab-platform/scripts/ego-derive" run \
    --session "${SESSION}" --json >"${derive_raw}" 2>&1; then
    fail "derive CLI failed run ${i}; see ${derive_raw}"
  fi
  python3 - "${derive_raw}" "${derive_json}" <<'PY'
import json, sys
raw = open(sys.argv[1]).read()
start = raw.find('{')
if start < 0:
    raise SystemExit('no JSON in derive output')
obj = json.loads(raw[start:])
open(sys.argv[2], 'w').write(json.dumps(obj, indent=2))
PY
  elapsed=$(( $(date +%s%3N) - t0 ))

  ok=$(python3 -c "import json; d=json.load(open('${derive_json}')); print('true' if d.get('ok') else 'false')")
  phase=$(python3 -c "import json; d=json.load(open('${derive_json}')); print(d.get('phase','?'))")
  checks=$(python3 -c "import json; d=json.load(open('${derive_json}')); g=d.get('gate') or {}; print(f\"{g.get('checksPassed','?')}/{g.get('checksTotal','?')}\")")

  [[ "${ok}" == "true" && "${phase}" == "READY" ]] \
    || fail "run ${i}: derive not READY (phase=${phase}); see ${derive_json}"

  metrics=$(collect_metrics "${i}")
  metrics=$(python3 -c "import json,sys; m=json.loads(sys.argv[1]); m['elapsed_ms']=int(sys.argv[2]); m['gate_checks']='${checks}'; print(json.dumps(m))" "${metrics}" "${elapsed}")
  RESULTS+=("${metrics}")
  log "run ${i} OK: ${metrics}"
done

log "=== summary ==="
python3 - "${RESULTS[@]}" <<'PY'
import json, sys
rows = [json.loads(a) for a in sys.argv[1:]]
keys = ["main_rows", "sensor_raw_rows", "sensor_raw_both_ok_pct", "g6_null_pct", "mp4_count", "elapsed_ms"]
print(f"{'run':>4}  {'phase':>8}  {'G6%':>6}  {'imu_rows':>8}  {'both_ok%':>8}  {'mp4':>3}  {'ms':>6}")
for r in rows:
    print(
        f"{r['run']:>4}  {'READY':>8}  {r['g6_null_pct']:>6.2f}  "
        f"{r['sensor_raw_rows']:>8}  {r['sensor_raw_both_ok_pct']:>7.1f}%  "
        f"{r['mp4_count']:>3}  {r['elapsed_ms']:>6}"
    )
stable = all(
    r["session_ready"]
    and r["mux_validated_ok"] is True
    and r["g6_null_pct"] == 0.0
    and r["sensor_raw_both_ok_pct"] == 100.0
    and r["main_rows"] == rows[0]["main_rows"]
    and r["sensor_raw_rows"] == rows[0]["sensor_raw_rows"]
    for r in rows
)
print("")
print(f"runs={len(rows)} all_READY={all(r['session_ready'] for r in rows)} metrics_stable={stable}")
if not stable:
    raise SystemExit(1)
PY

log "stability PASSED (${RUNS}/${RUNS})"
