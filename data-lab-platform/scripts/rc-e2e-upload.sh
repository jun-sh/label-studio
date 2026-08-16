#!/usr/bin/env bash
# RC-1 E2E: 130 upload (tar.zst) → 34 ingest → derive READY.
# Env: RC_STATION RC_SESSION RC_UPLOAD_LIMIT RC_CAPTURE_HOST RC_ASSERT_READY=1
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CID="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
STATION="${RC_STATION:-ego-001}"
SESSION="${RC_SESSION:-}"
STREAM_ROOT="${STREAM_ROOT:-${ROOT}/data-storage/stream}"
CAPTURE_HOST="${RC_CAPTURE_HOST:-10.10.10.130}"
CAPTURE_USER="${RC_CAPTURE_USER:-server}"
CAPTURE_PASS="${RC_CAPTURE_PASS:-1}"
LIMIT="${RC_UPLOAD_LIMIT:-3}"
ASSERT_READY="${RC_ASSERT_READY:-1}"
DERIVE_TIMEOUT_S="${RC_DERIVE_TIMEOUT_S:-300}"
SKIP_UPLOAD="${RC_SKIP_UPLOAD:-0}"

JSONL="${STREAM_ROOT}/${STATION}/data/chunk-000/file-000.jsonl"
ROWS_BEFORE=0
[[ -f "${JSONL}" ]] && ROWS_BEFORE=$(wc -l < "${JSONL}")
MP4_BEFORE=$(find "${STREAM_ROOT}/${STATION}/videos" -name '*.mp4' 2>/dev/null | wc -l)

echo "=== RC-1 upload ${LIMIT} segment(s) from ${CAPTURE_HOST} station=${STATION} ==="
UPLOAD_SESSION="${SESSION}"
if [[ "${SKIP_UPLOAD}" != "1" ]]; then
export RC_CAPTURE_HOST="${CAPTURE_HOST}" RC_CAPTURE_USER="${CAPTURE_USER}" RC_CAPTURE_PASS="${CAPTURE_PASS}" \
  RC_SESSION="${SESSION}" RC_UPLOAD_LIMIT="${LIMIT}" RC_STATION="${STATION}"
UPLOAD_SESSION=$(python3 - <<'PY'
import os, paramiko, sys

host = os.environ["RC_CAPTURE_HOST"]
user = os.environ["RC_CAPTURE_USER"]
password = os.environ["RC_CAPTURE_PASS"]
station = os.environ.get("RC_STATION", "ego-001")
session = os.environ.get("RC_SESSION", "").strip()
limit = int(os.environ.get("RC_UPLOAD_LIMIT", "3"))
seg_root = f"/home/server/cache/{station}/segments"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=password, timeout=15)

def run(cmd, timeout=120):
    _, o, e = c.exec_command(cmd, timeout=timeout)
    return (o.read() + e.read()).decode()

if not session:
    out = run(
        f"ls -td {seg_root}/sessions/sess_*/segments/seg_* 2>/dev/null | head -1"
    ).strip()
    if out:
        parts = out.split("/")
        for i, p in enumerate(parts):
            if p.startswith("sess_"):
                session = p
                break
    if not session and os.path.isfile(f"{seg_root}/checkpoint.json"):
        import json
        # discover via checkpoint over ssh cat
        raw = run(f"cat {seg_root}/checkpoint.json 2>/dev/null").strip()
        if raw:
            try:
                session = json.loads(raw).get("session_id") or ""
            except Exception:
                pass
if not session:
    print("FAIL: no RC_SESSION and cannot discover session on 130", file=sys.stderr)
    sys.exit(1)

print(f"session={session}", file=sys.stderr)
cmd = f"""bash -lc '
set -a; source /home/server/.config/ego-station.env 2>/dev/null || true; set +a
export PYTHONPATH=/home/server/workspace/ego-studio/src
/home/server/workspace/ego-studio/.venv/bin/python -m ego_capture_studio.cli.upload_segments \\
  --limit {limit} --ensure-session \\
  --segment-root {seg_root} \\
  --upload-url http://10.10.10.34:8080/lerobot/api/collection/stations/{station}/upload \\
  --session-id {session}
'"""
text = run(cmd, timeout=900)
sys.stderr.write(text)
if "uploaded_segments=" not in text:
    sys.exit(1)
uploaded = 0
for line in text.splitlines():
    if line.startswith("uploaded_segments="):
        uploaded = int(line.split("=", 1)[1].strip())
if uploaded < 1:
    sys.exit(1)
print(session)
c.close()
PY
)
UPLOAD_SESSION="${UPLOAD_SESSION//$'\n'/}"
UPLOAD_SESSION="${UPLOAD_SESSION##*$'\n'}"
UPLOAD_SESSION="${UPLOAD_SESSION//[[:space:]]/}"
fi
if [[ "${SKIP_UPLOAD}" == "1" ]]; then
  if [[ -z "${UPLOAD_SESSION}" && -n "${SESSION}" ]]; then
    UPLOAD_SESSION="${SESSION}"
  fi
  if [[ -z "${UPLOAD_SESSION}" ]]; then
    UPLOAD_SESSION="$(STREAM_ROOT="${STREAM_ROOT}" python3 - <<'PY'
import json
import os
from pathlib import Path
station = os.environ.get("RC_STATION", "ego-001")
root = Path(os.environ["STREAM_ROOT"]) / station
live = root / "live" / "session.json"
if live.is_file():
    sid = json.loads(live.read_text()).get("sessionId") or ""
    if sid:
        print(sid)
PY
)"
  fi
fi
if [[ -z "${UPLOAD_SESSION}" ]]; then
  echo "FAIL: no session (set RC_SESSION or RC_SKIP_UPLOAD=0 for upload)"
  exit 1
fi
echo "upload_session=${UPLOAD_SESSION}"

if [[ "${SKIP_UPLOAD}" != "1" ]]; then

echo "waiting for ingest (max 180s)..."
for _ in $(seq 1 36); do
  LOGS=$(docker logs "${CID}" 2>&1 | tail -120)
  if echo "${LOGS}" | grep -qE "segment_mp4_ingest|tarzst_segment_ok"; then
    if echo "${LOGS}" | grep -q "session=${UPLOAD_SESSION}"; then
      break
    fi
  fi
  sleep 5
done

ROWS_AFTER=0
[[ -f "${JSONL}" ]] && ROWS_AFTER=$(wc -l < "${JSONL}")
MP4_COUNT=$(find "${STREAM_ROOT}/${STATION}/videos" -name '*.mp4' 2>/dev/null | wc -l)

echo "jsonl rows: ${ROWS_BEFORE} -> ${ROWS_AFTER}"
echo "mp4 files: ${MP4_BEFORE} -> ${MP4_COUNT}"

test "${ROWS_AFTER}" -ge "${ROWS_BEFORE}" || { echo "FAIL: jsonl shrank"; exit 1; }
test "${MP4_COUNT}" -gt 0 || { echo "FAIL: no MP4 after upload"; exit 1; }
fi

if [[ "${SKIP_UPLOAD}" == "1" ]]; then
  MP4_COUNT=$(find "${STREAM_ROOT}/${STATION}/videos" -name '*.mp4' 2>/dev/null | wc -l)
  test "${MP4_COUNT}" -gt 0 || { echo "FAIL: no MP4 on disk"; exit 1; }
fi

if [[ "${ASSERT_READY}" == "1" ]]; then
  echo "=== RC-1b derive → READY (timeout ${DERIVE_TIMEOUT_S}s) ==="
  export STATION_ID="${STATION}"
  "${ROOT}/data-lab-platform/scripts/ego-derive" run --station "${STATION}" --session "${UPLOAD_SESSION}" 2>&1 | tail -5 || true

  deadline=$((SECONDS + DERIVE_TIMEOUT_S))
  phase=""
  while [[ "${SECONDS}" -lt "${deadline}" ]]; do
  phase=$(STATION_ID="${STATION}" "${ROOT}/data-lab-platform/scripts/ego-derive" status \
    --station "${STATION}" --session "${UPLOAD_SESSION}" --json 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('disk',{}).get('phase',''))" 2>/dev/null || echo "")
    if [[ "${phase}" == "READY" ]]; then
      break
    fi
    sleep 5
  done

  STATION_ID="${STATION}" "${ROOT}/data-lab-platform/scripts/ego-derive" status \
    --station "${STATION}" --session "${UPLOAD_SESSION}" --json > /tmp/rc-e2e-derive-status.json
  python3 - <<'PY'
import json, sys
d = json.load(open("/tmp/rc-e2e-derive-status.json"))
disk = d.get("disk", {})
phase = disk.get("phase")
mp4_ok = disk.get("mp4Ok")
parquet = disk.get("parquetRows", 0)
ready = d.get("sessionMarkers", {}).get("session.READY")
print(f"phase={phase} mp4Ok={mp4_ok} parquet={parquet} READY_marker={ready is not None}")
if phase != "READY" or not mp4_ok:
    sys.exit(1)
PY
  echo "RC-1b READY ok"
fi

MUX_VAL="${STREAM_ROOT}/${STATION}/live/derive/mux_validated.json"
test -f "${MUX_VAL}" || { echo "FAIL: mux_validated.json missing"; exit 1; }

echo "=== RC-1 E2E PASSED (session=${UPLOAD_SESSION}) ==="
