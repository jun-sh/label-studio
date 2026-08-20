#!/usr/bin/env bash
# Phase C acceptance: unit layout online + L1/L2/manifest/Viewer gates.
# Auto-detects episode/frame counts from manifest (post-wipe or multi-trip).
#
# Env:
#   MIN_EPISODES  minimum published episodes (default: read from manifest, at least 1)
#   MIN_FRAMES    minimum total frames (default: manifest total_frames)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
STREAM="${ROOT}/data-storage/stream/${STATION}"
HOST="${LABEL_STUDIO_HOST:-http://127.0.0.1:8080}"
HOST="${HOST%/}"
INGEST="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"

PASS=0
FAIL=0
log() { echo "[p2-accept] $*"; }
ok() { log "PASS: $*"; PASS=$((PASS + 1)); }
bad() { log "FAIL: $*"; FAIL=$((FAIL + 1)); }

log "=== P2 unit acceptance (${STATION}) ==="

layout="$(docker exec "${INGEST}" printenv DERIVE_LAYOUT 2>/dev/null || echo "?")"
[[ "${layout}" == "unit" ]] && ok "DERIVE_LAYOUT=unit" || bad "DERIVE_LAYOUT=${layout}"

DERIVE_LAYOUT=unit bash "${ROOT}/data-lab-platform/scripts/ego-derive" fsck --station "${STATION}" --json >/tmp/p2-fsck.json
python3 - /tmp/p2-fsck.json <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d.get("ok"), d
assert not d.get("issues"), d.get("issues")
m = d.get("manifest") or {}
assert len(m.get("episodes", [])) >= 1
assert int(m.get("total_frames") or 0) > 0
print("fsck ok episodes", len(m["episodes"]), "frames", m["total_frames"])
PY
ok "fsck manifest valid"

read -r EXP_EPISODES EXP_FRAMES MIN_EP MIN_FR <<<"$(python3 - "${STREAM}" "${MIN_EPISODES:-}" "${MIN_FRAMES:-}" <<'PY'
import json, os, sys
from pathlib import Path
root = Path(sys.argv[1])
min_ep = (sys.argv[2] or "").strip()
min_fr = (sys.argv[3] or "").strip()
manifest = json.loads((root / "manifest/manifest.json").read_text())
info = json.loads((root / "meta/info.json").read_text())
exp_ep = len(manifest["episodes"])
exp_fr = int(manifest["total_frames"])
min_ep_i = int(min_ep) if min_ep else exp_ep
min_fr_i = int(min_fr) if min_fr else exp_fr
print(exp_ep, exp_fr, min_ep_i, min_fr_i)
PY
)"
log "expect episodes=${EXP_EPISODES} frames=${EXP_FRAMES} (min_ep=${MIN_EP} min_fr=${MIN_FR})"

python3 - "${STREAM}" "${EXP_EPISODES}" "${EXP_FRAMES}" <<'PY'
import json, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
exp_ep = int(sys.argv[2])
exp_fr = int(sys.argv[3])
manifest = json.loads((root / "manifest/manifest.json").read_text())
info = json.loads((root / "meta/info.json").read_text())
units = len(list((root / "derived").glob("sess_*/unit.json")))
parquet = int(subprocess.check_output([
    "python3", "-c",
    "import sys; import pyarrow.parquet as pq; from pathlib import Path; r=Path(sys.argv[1]); print(sum(pq.read_metadata(p).num_rows for p in sorted(r.glob('data/**/*.parquet'))))",
    str(root),
], text=True).strip())
mp4 = sum(
    int(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", str(p),
    ], text=True).strip() or "0")
    for p in sorted((root / "videos/observation.images.camera_front_left").glob("**/*.mp4"))
)
assert units == exp_ep == len(manifest["episodes"])
assert manifest["total_frames"] == exp_fr == parquet == info["total_frames"]
assert mp4 == exp_fr
print(units, parquet, mp4)
PY
ok "L1/L2 aligned (${EXP_EPISODES} episodes, ${EXP_FRAMES} frames)"

[[ "${EXP_EPISODES}" -ge "${MIN_EP}" ]] && ok "episodes>=${MIN_EP}" || bad "episodes=${EXP_EPISODES} < min=${MIN_EP}"
[[ "${EXP_FRAMES}" -ge "${MIN_FR}" ]] && ok "frames>=${MIN_FR}" || bad "frames=${EXP_FRAMES} < min=${MIN_FR}"

curl -sf --max-time 15 "${HOST}/lerobot/api/collection/stations/${STATION}/derive-status" >/tmp/p2-derive-status.json
python3 - /tmp/p2-derive-status.json "${EXP_EPISODES}" "${EXP_FRAMES}" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
exp_ep = int(sys.argv[2])
exp_fr = int(sys.argv[3])
p = d["progress"]
assert d["phase"] == "READY"
assert p["markers"] == p["total"] == exp_ep
assert int(p["parquetRows"]) == exp_fr
assert p.get("mp4Ok") is True
print(d["phase"], p["markers"], p["parquetRows"])
PY
ok "derive-status READY ${EXP_EPISODES}/${EXP_EPISODES}"

curl -sf --max-time 15 "${HOST}/lerobot/api/stream/${STATION}/meta/info.json" >/tmp/p2-viewer-info.json
python3 - /tmp/p2-viewer-info.json "${EXP_EPISODES}" "${EXP_FRAMES}" <<'PY'
import json, sys
i = json.load(open(sys.argv[1]))
exp_ep = int(sys.argv[2])
exp_fr = int(sys.argv[3])
assert int(i.get("total_frames") or 0) == exp_fr
assert int(i.get("total_episodes") or 0) == exp_ep
print(i["total_frames"], i["total_episodes"])
PY
ok "Viewer stream info.json (${EXP_EPISODES} episodes)"

python3 - "${STREAM}" "${EXP_EPISODES}" <<'PY'
import json, sys
from pathlib import Path
import pyarrow.parquet as pq
root = Path(sys.argv[1])
exp_ep = int(sys.argv[2])
t = pq.read_table(root / "meta/episodes/chunk-000/file-000.parquet")
assert t.num_rows == exp_ep
print("episodes parquet rows", t.num_rows)
PY
ok "episodes metadata table"

log "=== summary: PASS=${PASS} FAIL=${FAIL} ==="
[[ "${FAIL}" -eq 0 ]] || exit 1
log "Phase C acceptance PASSED (${EXP_EPISODES} episodes, ${EXP_FRAMES} frames)"
exit 0
