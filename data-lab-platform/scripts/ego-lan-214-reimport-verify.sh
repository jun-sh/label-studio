#!/usr/bin/env bash
# ego-lan-214 acceptance: sync (P0) + optional Phase 1 async (Raw / derived / derive-retry).
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
EXPECTED_SEGMENTS="${EXPECTED_SEGMENTS:-21}"
EXPECTED_FRAMES="${EXPECTED_FRAMES:-6300}"
FPS="${EXPECTED_FPS:-30}"
TOLERANCE_FRAMES="${TOLERANCE_FRAMES:-5}"
# Set CHECK_DERIVE_ASYNC=1 for W2 gray (A2–A8). Default 0 = sync derive checks optional.
CHECK_DERIVE_ASYNC="${CHECK_DERIVE_ASYNC:-0}"
SKIP_DERIVE_RETRY_TEST="${SKIP_DERIVE_RETRY_TEST:-0}"
CONTAINER="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
TOKENS_JSON="${COLLECTION_STATION_TOKENS_JSON:-$(cd "$(dirname "$0")/../.." && pwd)/data-lab-platform/lerobot-studio/config/collection-station-tokens.json}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STREAM_HOST="${ROOT}/data-storage/stream/${STATION}"
API_BASE="${API_BASE:-http://127.0.0.1:8080}"
INGEST_INTERNAL="${INGEST_INTERNAL_URL:-http://127.0.0.1:7862}"

# EXPECTED_SEGMENTS=auto → count raw tar.zst (subset / partial upload friendly)
if [[ "${EXPECTED_SEGMENTS}" == "auto" ]]; then
  EXPECTED_SEGMENTS="$(find "${STREAM_HOST}/raw/segments" -name '*.tar.zst' 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "${EXPECTED_SEGMENTS}" -lt 1 ]]; then
    EXPECTED_SEGMENTS=21
  fi
fi
if [[ "${EXPECTED_FRAMES}" == "auto" ]]; then
  EXPECTED_FRAMES=$((EXPECTED_SEGMENTS * 300))
fi

CAMERAS=(
  observation.images.camera_front_left
  observation.images.camera_front_right
  observation.images.camera_rear_left
  observation.images.camera_rear_right
)

pass=0
fail=0
warn=0

ok() { echo "[PASS] $*"; pass=$((pass + 1)); }
bad() { echo "[FAIL] $*"; fail=$((fail + 1)); }
note() { echo "[WARN] $*"; warn=$((warn + 1)); }

ingest_curl() {
  docker exec "${CONTAINER}" wget -qO- "$@" 2>/dev/null
}

ingest_post() {
  local url="$1"
  local token="$2"
  docker exec "${CONTAINER}" wget -qO- \
    --header="X-Station-Token: ${token}" \
    --post-data="" \
    "${url}" 2>/dev/null
}

probe_frames() {
  local f="$1"
  ffprobe -v error -select_streams v:0 -count_frames \
    -show_entries stream=nb_read_frames,nb_frames,duration,r_frame_rate \
    -of json "$f" 2>/dev/null | python3 -c "
import json,sys,math
d=json.load(sys.stdin)
s=(d.get('streams') or [{}])[0]
nb=s.get('nb_read_frames') or s.get('nb_frames')
if nb not in (None,'N/A',''):
  print(int(float(nb))); sys.exit(0)
dur=float(s.get('duration') or 0)
rate=str(s.get('r_frame_rate') or '0/1')
a,b=rate.split('/')
fps=float(a)/float(b) if float(b) else ${FPS}
print(max(1, round(dur*fps)))
"
}

echo "=== ego-lan-214 verification ==="
echo "stream: ${STREAM_HOST}"
echo "expected: ${EXPECTED_SEGMENTS} segments, ~${EXPECTED_FRAMES} frames"
if [[ "${EXPECTED_SEGMENTS}" != "21" ]] && find "${STREAM_HOST}/raw/segments" -name '*.tar.zst' 2>/dev/null | grep -q .; then
  note "subset mode: validating ${EXPECTED_SEGMENTS} uploaded raw segments (not fixed 21)"
fi
echo "CHECK_DERIVE_ASYNC=${CHECK_DERIVE_ASYNC}"
echo ""

# --- Core derived-layer checks (A5 / sync + async) ---

if status_json="$(curl -sf "${API_BASE}/lerobot/api/stream/${STATION}/status" 2>/dev/null)"; then
  total="$(echo "$status_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('totalFrames',0))")"
  ingest="$(echo "$status_json" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ingestFrames',0))")"
  if [[ "$total" -ge $((EXPECTED_FRAMES - TOLERANCE_FRAMES)) ]]; then
    ok "API totalFrames=${total} ingestFrames=${ingest}"
  else
    bad "API totalFrames=${total} (expected >= $((EXPECTED_FRAMES - TOLERANCE_FRAMES)))"
  fi
else
  bad "stream status API unreachable at ${API_BASE}"
fi

if [[ -d "${STREAM_HOST}/live/sessions" ]]; then
  done_count="$(find "${STREAM_HOST}/live/sessions" -name '*.done' 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "$done_count" -eq "$EXPECTED_SEGMENTS" ]]; then
    ok "segment .done count=${done_count}"
  else
    bad "segment .done count=${done_count} (expected ${EXPECTED_SEGMENTS})"
  fi
else
  bad "live/sessions missing under stream root"
fi

jsonl="${STREAM_HOST}/data/chunk-000/file-000.jsonl"
if [[ -f "$jsonl" ]]; then
  lines="$(wc -l < "$jsonl" | tr -d ' ')"
  if [[ "$lines" -ge $((EXPECTED_FRAMES - TOLERANCE_FRAMES)) ]]; then
    ok "jsonl lines=${lines}"
  else
    bad "jsonl lines=${lines} (expected ~${EXPECTED_FRAMES})"
  fi
else
  bad "jsonl missing: ${jsonl}"
fi

parquet="${STREAM_HOST}/data/chunk-000/file-000.parquet"
if [[ -f "$parquet" && -f "$jsonl" ]]; then
  pq_rows=$(python3 -c "import pyarrow.parquet as pq; print(pq.read_metadata('$parquet').num_rows)")
  if [[ "$pq_rows" -eq "$lines" ]]; then
    ok "parquet rows=${pq_rows} matches jsonl"
  else
    bad "parquet rows=${pq_rows} jsonl=${lines}"
  fi
fi

for cam in "${CAMERAS[@]}"; do
  mp4="${STREAM_HOST}/videos/${cam}/chunk-000/file-000.mp4"
  if [[ ! -f "$mp4" ]]; then
    bad "mp4 missing: ${cam}"
    continue
  fi
  frames="$(probe_frames "$mp4")"
  if [[ "$frames" -ge $((EXPECTED_FRAMES - TOLERANCE_FRAMES)) ]]; then
    ok "${cam}: ${frames} frames"
  else
    bad "${cam}: ${frames} frames (expected ~${EXPECTED_FRAMES})"
  fi
done

chunks="${STREAM_HOST}/live/chunks.json"
if [[ -f "$chunks" ]]; then
  if python3 - "$chunks" "$EXPECTED_FRAMES" <<'PY'
import json, sys
path, expected = sys.argv[1], int(sys.argv[2])
data = json.load(open(path))
pub = data.get("publish") or {}
bad = []
for rel, st in pub.items():
    if not rel.startswith("videos/") or not rel.endswith(".mp4"):
        continue
    frames = int(st.get("frames") or 0)
    status = st.get("status")
    if status == "finished" and frames < expected - 5:
        bad.append(f"{rel}: status=finished frames={frames}")
if bad:
    print("[FAIL] chunks.json false finished:")
    for line in bad:
        print("       ", line)
    sys.exit(1)
PY
  then
    ok "chunks.json video publish flags"
  else
    bad "chunks.json video publish flags inconsistent"
  fi
else
  note "chunks.json not found (may appear after first publish cycle)"
fi

mux_state="${STREAM_HOST}/live/mux-state.json"
video_export_backend="$(docker exec "${CONTAINER}" printenv DERIVE_VIDEO_EXPORT_BACKEND 2>/dev/null || true)"
if [[ "${video_export_backend}" == "auto" || "${video_export_backend}" == "streaming" || "${video_export_backend}" == "parquet" || "${video_export_backend}" == "lerobot" ]]; then
  if [[ -f "$mux_state" ]]; then
    note "legacy mux-state.json present under Phase B export (safe to delete)"
  else
    ok "Phase B export: no mux-state.json"
  fi
elif [[ -f "$mux_state" ]]; then
  if python3 - "$mux_state" "$STREAM_HOST" <<'PY'
import json, sys, subprocess, pathlib
state_path, root = sys.argv[1], pathlib.Path(sys.argv[2])
state = json.load(open(state_path))
cameras = state.get("cameras") or {}
failed = []
for key, cam in cameras.items():
    rel = f"videos/{key}/chunk-000/file-000.mp4"
    mp4 = root / rel
    if not mp4.is_file():
        failed.append(f"{key}: mux-state present but mp4 missing")
        continue
    claimed = int(cam.get("verifiedFrames") or (int(cam.get("lastMuxedFrame", -1)) + 1))
    proc = subprocess.run(
        ["ffprobe","-v","error","-select_streams","v:0","-count_frames",
         "-show_entries","stream=nb_read_frames","-of","csv=p=0", str(mp4)],
        capture_output=True, text=True)
    disk = int((proc.stdout or "0").strip() or 0)
    if abs(disk - claimed) > 5:
        failed.append(f"{key}: mux-state={claimed} disk={disk}")
if failed:
    print("[FAIL] mux-state vs disk mismatch:")
    for line in failed:
        print("       ", line)
    sys.exit(1)
PY
  then
    ok "mux-state reconciled with disk"
  else
    bad "mux-state vs disk mismatch"
  fi
else
  note "mux-state.json not found yet"
fi

# --- Phase 1 async checks (A2–A4, A7–A8) when CHECK_DERIVE_ASYNC=1 ---

if [[ "${CHECK_DERIVE_ASYNC}" == "1" ]]; then
  echo ""
  echo "=== Phase 1 async / Raw checks ==="

  async_env="$(docker exec "${CONTAINER}" printenv DERIVE_ASYNC_EGO_LAN_214 2>/dev/null || true)"
  global_env="$(docker exec "${CONTAINER}" printenv DERIVE_ASYNC 2>/dev/null || true)"
  if [[ "${async_env}" == "1" || "${global_env}" == "1" ]]; then
    ok "DERIVE_ASYNC gray enabled (station=${async_env:-0} global=${global_env:-0})"
  else
    bad "CHECK_DERIVE_ASYNC=1 but DERIVE_ASYNC_EGO_LAN_214/DERIVE_ASYNC not 1 in container"
  fi

  raw_count="$(find "${STREAM_HOST}/raw/segments" -name '*.tar.zst' 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "$raw_count" -ge "$EXPECTED_SEGMENTS" ]]; then
    ok "raw tar.zst count=${raw_count} (>= ${EXPECTED_SEGMENTS})"
  else
    bad "raw tar.zst count=${raw_count} (expected >= ${EXPECTED_SEGMENTS})"
  fi

  if python3 - "$STREAM_HOST" "$EXPECTED_SEGMENTS" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
expected = int(sys.argv[2])
raw_root = root / "raw" / "segments"
manifest_root = root / "raw" / "manifest"
errors = []
count = 0
for tar in sorted(raw_root.rglob("*.tar.zst")):
    count += 1
    rel = tar.relative_to(raw_root)
    parts = rel.parts
    if len(parts) < 2:
        errors.append(f"{tar}: bad path layout")
        continue
    session_id, name = parts[0], parts[1]
    segment_id = name.replace(".tar.zst", "")
    manifest_path = manifest_root / session_id / f"{segment_id}.json"
    if not manifest_path.is_file():
        errors.append(f"{tar}: manifest missing")
        continue
    manifest = json.loads(manifest_path.read_text())
    sha = manifest.get("sha256", "")
    h = hashlib.sha256()
    with open(tar, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if sha and sha != actual:
        errors.append(f"{segment_id}: sha256 mismatch manifest vs disk")
    if manifest.get("segmentId") and manifest["segmentId"] != segment_id:
        errors.append(f"{segment_id}: manifest segmentId mismatch")
if count < expected:
    errors.append(f"only {count} raw archives (expected >={expected})")
if errors:
    print("[FAIL] raw integrity:")
    for e in errors:
        print("       ", e)
    sys.exit(1)
PY
  then
    ok "raw manifest sha256 integrity"
  else
    bad "raw manifest sha256 integrity"
  fi

  seg_json="${STREAM_HOST}/state/segments.json"
  if [[ -f "$seg_json" ]]; then
    if python3 - "$seg_json" "$EXPECTED_SEGMENTS" <<'PY'
import json, sys
from pathlib import Path
path, expected = sys.argv[1], int(sys.argv[2])
store = json.load(open(path))
segments = list((store.get("segments") or {}).values())
ready = []
derived_only = []
mux_path = Path(sys.argv[1]).parent.parent / "live" / "derive" / "mux_validated.json"
markers_dir = Path(sys.argv[1]).parent.parent / "live" / "derive" / "markers"
for s in segments:
    if s.get("inDlq"):
        continue
    sid = s.get("sessionId")
    seg = s.get("segmentId")
    marker = markers_dir / str(sid) / f"{seg}.ok.json" if sid and seg else None
    if marker and marker.is_file():
        derived_only.append(s)
mux_ok = False
if mux_path.is_file():
    try:
        mux = json.load(open(mux_path))
        mux_ok = bool(mux.get("ok"))
    except Exception:
        pass
if mux_ok and len(derived_only) >= expected:
    ready = derived_only
failed = [
    s for s in segments
    if s.get("status") in ("derive_failed", "failed")
    or s.get("inDlq")
]
if len(ready) < expected:
    print(f"[FAIL] READY(ready=true) count={len(ready)} expected>={expected} derived_pending={len(derived_only)} derive_failed={len(failed)}")
    for s in failed[:5]:
        print(f"       failed: {s.get('segmentId')} {s.get('errorMsg')}")
    sys.exit(1)
if failed:
    print(f"[FAIL] segment state has {len(failed)} DERIVE_FAILED while only {len(ready)} READY")
    sys.exit(1)
PY
    then
      ok "segment state: ${EXPECTED_SEGMENTS} READY (disk: markers + mux_validated)"
    else
      bad "segment state derived count"
    fi
  else
    bad "state/segments.json missing"
  fi

  if segments_api="$(ingest_curl "${INGEST_INTERNAL}/lerobot/api/collection/stations/${STATION}/segments?status=derived")"; then
    api_derived="$(echo "$segments_api" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('segments',[])))")"
    if [[ "$api_derived" -ge "$EXPECTED_SEGMENTS" ]]; then
      ok "segments API derived count=${api_derived}"
    else
      bad "segments API derived count=${api_derived} (expected >= ${EXPECTED_SEGMENTS})"
    fi
  else
    bad "segments API unreachable via ${CONTAINER}"
  fi

  if [[ "${SKIP_DERIVE_RETRY_TEST}" != "1" ]]; then
    token="$(python3 -c "import json; print(json.load(open('${TOKENS_JSON}')).get('${STATION}',''))")"
    if [[ -z "$token" ]]; then
      bad "derive-retry: no station token in ${TOKENS_JSON}"
    else
      retry_target="$(python3 - "$seg_json" <<'PY'
import json, sys
store = json.load(open(sys.argv[1]))
segs = sorted((store.get("segments") or {}).values(), key=lambda s: s.get("segmentId") or "")
for s in segs:
    if s.get("status") == "derived" and s.get("sessionId") and s.get("segmentId"):
        print(s["sessionId"], s["segmentId"])
        break
PY
)"
      if [[ -z "$retry_target" ]]; then
        bad "derive-retry: no derived segment to test"
      else
        read -r retry_sess retry_seg <<< "$retry_target"
        retry_url="${INGEST_INTERNAL}/lerobot/api/collection/stations/${STATION}/segments/${retry_sess}/${retry_seg}/derive-retry"
        if retry_resp="$(ingest_post "${retry_url}" "${token}")"; then
          if echo "$retry_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('queued') else 1)"; then
            ok "derive-retry API queued (${retry_seg})"
          else
            bad "derive-retry API unexpected response: ${retry_resp}"
          fi
        else
          bad "derive-retry API request failed"
        fi
      fi
    fi
  else
    note "derive-retry test skipped (SKIP_DERIVE_RETRY_TEST=1)"
  fi
fi

echo ""
echo "=== Manual checks (operator) ==="
echo "  [ ] Collection replay: timeline + 4 panes in sync"
if [[ "${CHECK_DERIVE_ASYNC}" == "1" ]]; then
  echo "  [ ] W2: 21 segments uploaded via 214 ecs-oak-upload-stack (not browser batch)"
else
  echo "  [ ] Sync mode: DERIVE_ASYNC=0 — run ego-lan-214-sync-regression-smoke.sh before gray"
fi
echo ""
echo "=== Summary: ${pass} passed, ${fail} failed, ${warn} warnings ==="
if [[ "$fail" -gt 0 ]]; then
  exit 1
fi
