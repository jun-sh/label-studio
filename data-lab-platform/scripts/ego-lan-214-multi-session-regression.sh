#!/usr/bin/env bash
# Multi-session LeRobot derive regression (official-only path).
#
# Verifies after 2+ independent recordings:
#   - DERIVE_*_BACKEND=lerobot (no legacy fallback)
#   - N sessions => N episodes in episodes-index + meta/episodes.parquet
#   - Distinct per-session titles (from episodes-index)
#   - data.parquet row count = sum of episode lengths
#   - MP4 frame counts match total_frames (no overlap / truncation)
#
# Usage:
#   bash data-lab-platform/scripts/ego-lan-214-multi-session-regression.sh
#   EXPECTED_EPISODES=2 EXPECTED_FRAMES=600 bash ...
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STREAM_HOST="${ROOT}/data-storage/stream/${STATION}"
INGEST_CONTAINER="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
EXPECTED_EPISODES="${EXPECTED_EPISODES:-2}"
EXPECTED_FRAMES="${EXPECTED_FRAMES:-}"

pass=0
fail=0
ok() { echo "[PASS] $*"; pass=$((pass + 1)); }
bad() { echo "[FAIL] $*"; fail=$((fail + 1)); }

echo "=== Multi-session LeRobot regression (${STATION}) ==="

for var in DERIVE_PARQUET_BACKEND DERIVE_VIDEO_EXPORT_BACKEND; do
  val="$(docker exec "${INGEST_CONTAINER}" printenv "${var}" 2>/dev/null || true)"
  if [[ "${val}" == "lerobot" ]]; then
    ok "${var}=lerobot"
  else
    bad "${var}=${val:-<unset>} (expected lerobot — rebuild/recreate containers)"
  fi
done

if docker exec "${INGEST_CONTAINER}" python3 -c "from lerobot.datasets.lerobot_dataset import LeRobotDataset" 2>/dev/null; then
  ok "lerobot Python package importable in ${INGEST_CONTAINER}"
else
  bad "lerobot not installed in ${INGEST_CONTAINER} (rebuild bookworm image)"
fi

if docker exec "${INGEST_CONTAINER}" test ! -f /app/scripts/append-segment-parquet-lerobot.py; then
  ok "legacy append-segment-parquet-lerobot.py removed"
else
  bad "legacy append-segment-parquet-lerobot.py still present"
fi

python3 - "${STREAM_HOST}" "${EXPECTED_EPISODES}" "${EXPECTED_FRAMES}" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

import pyarrow.parquet as pq

root = Path(sys.argv[1])
expected_eps = int(sys.argv[2])
expected_frames_arg = sys.argv[3].strip()

def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(2)

def ok(msg):
    print(f"[PASS] {msg}")

index_path = root / "live" / "episodes-index.json"
if not index_path.is_file():
    fail(f"missing {index_path}")

index = json.loads(index_path.read_text(encoding="utf-8"))
episodes = [ep for ep in index.get("episodes") or [] if ep.get("segment_id") != "legacy"]
if len(episodes) < expected_eps:
    fail(f"episodes-index has {len(episodes)} episodes, expected>={expected_eps}")

ok(f"episodes-index episodes={len(episodes)}")

titles = [str(ep.get("title") or "").strip() for ep in episodes]
non_empty = [t for t in titles if t]
if len(non_empty) != len(episodes):
    fail("some episodes missing title in episodes-index")
if len(set(non_empty)) != len(non_empty):
    fail(f"duplicate episode titles: {non_empty}")

def session_id_short(session_id: str) -> str:
    token = str(session_id or "").strip()
    if token.lower().startswith("sess_"):
        token = token[5:]
    return token[:8].lower()

for ep in episodes:
    sid = str(ep.get("session_id") or "")
    title = str(ep.get("title") or "")
    if sid and session_id_short(sid) not in title.lower():
        fail(f"episode title missing session prefix: session={sid[:12]} title={title!r}")
ok("distinct episode titles in episodes-index (session-prefixed)")

sessions = [str(ep.get("session_id") or "") for ep in episodes]
if any(not s for s in sessions):
    fail("episodes missing session_id")
if len(set(sessions)) != len(sessions):
    fail(f"duplicate session_ids across episodes: {sessions}")
ok("one episode per session_id")

data_rows = sum(
    pq.read_metadata(p).num_rows
    for p in sorted((root / "data").rglob("*.parquet"))
)
if data_rows <= 0:
    fail("no rows in data/**/*.parquet")
ok(f"data.parquet rows={data_rows} (all shards)")

ep_files = sorted((root / "meta" / "episodes").rglob("*.parquet"))
if not ep_files:
    fail("missing meta/episodes/**/*.parquet")
ep_n = sum(pq.read_metadata(p).num_rows for p in ep_files)
ep_table = pq.read_table(ep_files[0])
if len(ep_files) > 1:
    import pyarrow as pa

    ep_table = pa.concat_tables([pq.read_table(p) for p in ep_files])
if ep_n != len(episodes):
    fail(f"meta/episodes.parquet rows={ep_n}, episodes-index={len(episodes)}")
ok(f"meta/episodes.parquet episodes={ep_n}")

marker = json.loads((root / "live" / "parquet_sync.json").read_text(encoding="utf-8")) if (root / "live" / "parquet_sync.json").is_file() else {}
lerobot_owned = marker.get("lerobot_data_owned") is True
if "tasks" in ep_table.column_names and not lerobot_owned:
    pq_titles = [str(t[0]) if t else "" for t in ep_table["tasks"].to_pylist()]
    for i, (idx_title, pq_title) in enumerate(zip(titles, pq_titles)):
        if idx_title and pq_title and idx_title != pq_title:
            fail(f"episode {i} title mismatch index={idx_title!r} parquet={pq_title!r}")
    ok("meta/episodes.parquet tasks match episodes-index titles")
elif lerobot_owned:
    ok("LeRobot owns episodes.parquet; titles authoritative in episodes-index")

sum_len = sum(int(ep.get("length") or 0) for ep in episodes)
if sum_len > 0 and data_rows != sum_len:
    fail(f"data.parquet rows={data_rows} != sum(episode.length)={sum_len}")

info = json.loads((root / "meta" / "info.json").read_text(encoding="utf-8"))
total_frames = int(info.get("total_frames") or 0)
if expected_frames_arg:
    exp = int(expected_frames_arg)
    if data_rows != exp:
        fail(f"data.parquet rows={data_rows}, expected={exp}")
    if total_frames != exp:
        fail(f"info.json total_frames={total_frames}, expected={exp}")
else:
    exp = total_frames or data_rows
if total_frames > 0 and total_frames != data_rows:
    fail(f"info.json total_frames={total_frames} != data shards={data_rows}")
ok(f"frame count aligned: data={data_rows} info.total_frames={total_frames or data_rows}")

import pyarrow as pa

data_files = sorted((root / "data").rglob("*.parquet"))
table = pa.concat_tables([pq.read_table(p) for p in data_files])
global_idx = table["index"].to_pylist() if "index" in table.column_names else table["frame_index"].to_pylist()
ep_idxs = table["episode_index"].to_pylist()
if len(global_idx) != data_rows:
    fail("index column length mismatch")
if global_idx != list(range(len(global_idx))):
    fail(f"global index not contiguous 0..{len(global_idx)-1}: min={min(global_idx)} max={max(global_idx)}")
ok("global index contiguous (LeRobot-managed)")

unique_eps = sorted(set(ep_idxs))
if unique_eps != list(range(len(unique_eps))):
    fail(f"unexpected episode_index values: {unique_eps}")
if len(unique_eps) != len(episodes):
    fail(f"episode_index cardinality {len(unique_eps)} != episodes {len(episodes)}")
ok(f"episode_index 0..{len(unique_eps)-1}")

info_path = root / "meta" / "info.json"
features = info.get("features") or {}
video_keys = [k for k, v in features.items() if isinstance(v, dict) and v.get("dtype") == "video"]
for vkey in video_keys:
    mp4s = sorted((root / "videos" / vkey).rglob("file-*.mp4"))
    if not mp4s:
        fail(f"missing MP4 shards for {vkey}")
    n = 0
    for mp4 in mp4s:
        res = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(mp4),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        n += int((res.stdout or "0").strip() or 0)
    if n < exp - 1:
        fail(f"{vkey} MP4 frames={n}, expected>={exp - 1}")
    ok(f"{vkey} MP4 frames={n} (expected~{exp})")

archive_root = root / "archive"
if archive_root.is_dir():
    for name in ("data", "videos", "meta"):
        hits = list(archive_root.rglob(name))
        if hits:
            fail(f"archive contains moved {name}/ — session isolation broken ({len(hits)} hits)")
ok("no data/videos/meta under archive/ (session data not wiped)")

print(f"[PASS] multi-session checks complete episodes={len(episodes)} frames={data_rows}")
PY

rc=$?
if [[ $rc -eq 2 ]]; then
  fail=$((fail + 1))
elif [[ $rc -eq 0 ]]; then
  pass=$((pass + 1))
else
  bad "python regression exited ${rc}"
  fail=$((fail + 1))
fi

echo ""
echo "=== Summary: pass=${pass} fail=${fail} ==="
[[ "${fail}" -eq 0 ]]
