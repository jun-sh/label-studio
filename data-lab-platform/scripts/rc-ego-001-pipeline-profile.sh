#!/usr/bin/env bash
# Segment pipeline wall-clock profiler (130 upload → 34 ingest → derive READY).
# No feat-branch code changes; parses docker logs + disk markers.
#
# Usage:
#   bash rc-ego-001-pipeline-profile.sh --session sess_xxx
#   bash rc-ego-001-pipeline-profile.sh --session sess_xxx --derive-only
#   bash rc-ego-001-pipeline-profile.sh --compare-local   # local single-session baseline hint
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
STREAM="${ROOT}/data-storage/stream/${STATION}"
INGEST="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
SESSION=""
DERIVE_ONLY=0
COMPARE_LOCAL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="$2"; shift 2 ;;
    --derive-only) DERIVE_ONLY=1; shift ;;
    --compare-local) COMPARE_LOCAL=1; shift ;;
    -h|--help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

log() { echo "[pipeline-profile] $*"; }

if [[ "${COMPARE_LOCAL}" == "1" ]]; then
  log "=== local single-session baseline (from stability runs) ==="
  cat <<'EOF'
参考基线（单 session ~304 帧，wipe→re-derive，无跨节点）:
  derive READY: ~23s（rc-ego-001-single-session-stability.sh 5 轮均值）
  构成: raw 解压 + staging + IMU ingest + 主表 + 四路 mux + ready-gate
跨节点完整链路额外开销（~246 帧/session，P0 实测）:
  采集+冷却:        ~20s
  130 tar+upload:    ~2s
  ingest 轮询等待:  0–180s（脚本轮询，非 ingest 本身）
  34 tar ingest:     ~2–5s（tarzst_segment_ok.elapsedMs）
  derive（单 session）: ~50–62s
  derive（N session 累积）: ~62s → 99s+（随历史 session 线性恶化）
  preflight:         ~2s
  合计 P0 单趟:      ~276–301s（4.6–5.0 min）
EOF
  exit 0
fi

[[ -n "${SESSION}" ]] || {
  SESSION="$(ls -t "${STREAM}/state/sessions"/sess_*/session.READY 2>/dev/null | head -1 | xargs -I{} dirname {} | xargs basename)" || true
}
[[ -n "${SESSION}" ]] || { echo "no session; pass --session sess_xxx" >&2; exit 2; }

log "station=${STATION} session=${SESSION}"

python3 - "${STREAM}" "${SESSION}" "${INGEST}" "${DERIVE_ONLY}" <<'PY'
import json, re, subprocess, sys
from datetime import datetime
from pathlib import Path

stream = Path(sys.argv[1])
session = sys.argv[2]
ingest_cid = sys.argv[3]
derive_only = sys.argv[4] == "1"

def parse_ts(s):
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None

def read_marker(name):
    p = stream / "state" / "sessions" / session / name
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"at": p.stat().st_mtime}

done = read_marker("session.DONE_UPLOAD")
ready = read_marker("session.READY")
failed = read_marker("session.FAILED")

# docker logs (last 48h)
try:
    logs = subprocess.check_output(
        ["docker", "logs", ingest_cid, "--since", "48h"],
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
except Exception as e:
    logs = f"(docker logs failed: {e})"

events = []
for line in logs.splitlines():
    if session not in line:
        continue
    m = re.search(r"event=(\w+)", line)
    if m:
        events.append((line, m.group(1)))
    if "tarzst_segment_ok" in line or "derive_" in line or "ego_derive_cli_start" in line:
        events.append((line, "log"))

# extract tarzst elapsedMs
ingest_ms = None
for line, _ in events:
    if "tarzst_segment_ok" in line and session in line:
        m = re.search(r"elapsedMs[:\s]+(\d+)", line)
        if m:
            ingest_ms = int(m.group(1))

# raw archive size
raw_dir = stream / "raw" / "segments" / session
raw_bytes = sum(p.stat().st_size for p in raw_dir.glob("*.tar.zst")) if raw_dir.is_dir() else 0
frame_count = ready.get("total") if ready else None
segments_n = len(list(raw_dir.glob("*.tar.zst"))) if raw_dir.is_dir() else 0

# global frame_map breadth (derive re-process scope)
fm_path = stream / "meta" / "episodes" / "episode_000.json"
fm_segments = 0
if fm_path.is_file():
    try:
        fm_segments = len(json.loads(fm_path.read_text()).get("frame_segments", []))
    except Exception:
        pass

# marker deltas
t_upload = parse_ts(done.get("at")) if done and done.get("at") else None
t_ready = parse_ts(ready.get("at")) if ready and ready.get("at") else None
derive_wall_s = (t_ready - t_upload).total_seconds() if t_upload and t_ready else None

print("=== session markers ===")
print(f"  DONE_UPLOAD: {done.get('at') if done else 'missing'}")
print(f"  READY:       {ready.get('at') if ready else 'missing'} phase={ready.get('phase') if ready else '?'}")
print(f"  gate:        {ready.get('ready_gate') if ready else 'n/a'}")
print(f"  frames(total in marker): {frame_count}")
print(f"  FAILED:      {'yes ' + str(failed.get('reason',{}).get('code','')) if failed else 'no'}")

print("\n=== raw archive ===")
print(f"  segments: {segments_n}  bytes: {raw_bytes:,}  (~{raw_bytes/1024/1024:.1f} MiB)")

print("\n=== ingest (34) ===")
print(f"  tarzst_segment_ok.elapsedMs: {ingest_ms if ingest_ms is not None else 'not found in logs'}")

print("\n=== derive scope ===")
print(f"  frame_map.frame_segments count: {fm_segments}")
if fm_segments > segments_n:
    print(f"  WARNING: derive re-processes {fm_segments} segments (not just this session's {segments_n})")
    print("           → cumulative re-derive; expect derive time to grow with history")

print("\n=== derive wall (marker DONE_UPLOAD → READY) ===")
if derive_wall_s is not None:
    print(f"  {derive_wall_s:.1f}s")
else:
    print("  n/a (markers missing timestamps)")

print("\n=== derive event trace (session-filtered) ===")
seen = set()
for line, _ in events[-30:]:
    key = line[:120]
    if key in seen:
        continue
    seen.add(key)
    print(" ", line[:200])

print("\n=== bottleneck hints ===")
hints = []
if ingest_ms and ingest_ms > 15000:
    hints.append("ingest >15s: check tar size / disk IO / zstd decompress")
if derive_wall_s and derive_wall_s > 90:
    hints.append("derive >90s: likely cumulative re-derive over all frame_segments")
if fm_segments > 3:
    hints.append(f"{fm_segments} historical segments in frame_map — consider episode isolation per SINGLE_EPISODE=0 SOP")
if not ready:
    hints.append("not READY — check session.FAILED and deriver.lock")
if not hints:
    hints.append("within expected P0 band if single-session ~50-65s derive")
for h in hints:
    print(f"  - {h}")

print("\n=== sub-step timing (fix2 built-in) ===")
print("  derive pipeline: event logs only (derive_segment_start → main_table_done → mux_done → derive_ready)")
print("  ingest: tarzst_segment_ok.elapsedMs in stream-ingest logs")
print("  no per-stage metrics export / OpenTelemetry — use this script + marker timestamps")
PY

log "done"
