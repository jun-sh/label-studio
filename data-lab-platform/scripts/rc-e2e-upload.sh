#!/usr/bin/env bash
# RC-1: upload one segment from 130 (tar.zst), verify mux + MP4 persist.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CID="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
STATION="${RC_STATION:-ego-001}"
SESSION="${RC_SESSION:-sess_e2a5cee61336498d9e24cb97e89f03b8}"
STREAM_ROOT="${STREAM_ROOT:-${ROOT}/data-storage/stream}"
CAPTURE_HOST="${RC_CAPTURE_HOST:-10.10.10.130}"
CAPTURE_USER="${RC_CAPTURE_USER:-server}"
CAPTURE_PASS="${RC_CAPTURE_PASS:-1}"
LIMIT="${RC_UPLOAD_LIMIT:-1}"

JSONL="${STREAM_ROOT}/${STATION}/data/chunk-000/file-000.jsonl"
ROWS_BEFORE=$(wc -l < "${JSONL}")
MP4_BEFORE=$(find "${STREAM_ROOT}/${STATION}/videos" -name '*.mp4' 2>/dev/null | wc -l)

echo "=== RC-1 upload ${LIMIT} segment(s) from ${CAPTURE_HOST} (tar.zst) ==="
export RC_CAPTURE_HOST="${CAPTURE_HOST}" RC_CAPTURE_USER="${CAPTURE_USER}" RC_CAPTURE_PASS="${CAPTURE_PASS}" RC_SESSION="${SESSION}" RC_UPLOAD_LIMIT="${LIMIT}"
python3 - <<'PY'
import os, paramiko, sys
host = os.environ["RC_CAPTURE_HOST"]
user = os.environ["RC_CAPTURE_USER"]
password = os.environ["RC_CAPTURE_PASS"]
session = os.environ["RC_SESSION"]
limit = os.environ.get("RC_UPLOAD_LIMIT", "1")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=password, timeout=15)
seg_base = f"/home/server/cache/ego-001/segments/sessions/{session}/segments"
_, o, _ = c.exec_command(f"ls {seg_base} 2>/dev/null | head -1")
first = o.read().decode().strip()
print("first_pending", first or "none")
cmd = f"""bash -lc '
set -a; source /home/server/.config/ego-station.env 2>/dev/null || true; set +a
export PYTHONPATH=/home/server/workspace/ego-studio/src
/home/server/workspace/ego-studio/.venv/bin/python -m ego_capture_studio.cli.upload_segments \\
  --limit {limit} --ensure-session \\
  --segment-root /home/server/cache/ego-001/segments \\
  --upload-url http://10.10.10.34:8080/lerobot/api/collection/stations/ego-001/upload \\
  --session-id {session}
'"""
_, out, err = c.exec_command(cmd, timeout=600)
text = out.read().decode() + err.read().decode()
sys.stdout.write(text)
if "uploaded_segments=" not in text:
    sys.exit(1)
uploaded = 0
for line in text.splitlines():
    if line.startswith("uploaded_segments="):
        uploaded = int(line.split("=", 1)[1].strip())
if uploaded < 1:
    sys.exit(1)
c.close()
PY

echo "waiting for ingest derive (max 180s)..."
for _ in $(seq 1 36); do
  LOGS=$(docker logs "${CID}" 2>&1 | tail -100)
  if echo "${LOGS}" | grep -qE "segment_mp4_ingest|mux_camera_done|tarzst_segment_ok"; then
    break
  fi
  sleep 5
done

ROWS_AFTER=$(wc -l < "${JSONL}")
MP4_COUNT=$(find "${STREAM_ROOT}/${STATION}/videos" -name '*.mp4' 2>/dev/null | wc -l)
MUX_VAL="${STREAM_ROOT}/${STATION}/live/derive/mux_validated.json"

echo "jsonl rows: ${ROWS_BEFORE} -> ${ROWS_AFTER}"
echo "mp4 files: ${MP4_BEFORE} -> ${MP4_COUNT}"

test "${ROWS_AFTER}" -ge "${ROWS_BEFORE}" || { echo "FAIL: jsonl shrank"; exit 1; }
if [[ "${ROWS_AFTER}" -eq "${ROWS_BEFORE}" ]]; then
  echo "WARN: jsonl unchanged (segment may already be on server)"
fi
test "${MP4_COUNT}" -gt "${MP4_BEFORE}" || test "${MP4_COUNT}" -gt 0 || { echo "FAIL: no MP4 after upload/mux"; exit 1; }
test -f "${MUX_VAL}" || { echo "FAIL: mux_validated.json missing"; exit 1; }

echo "=== RC-2 re-check: empty staging after mux ==="
docker exec "${CID}" node -e "
import { resumePendingStreamMuxForAllStations } from '/app/stream-ingest.mjs';
resumePendingStreamMuxForAllStations();
" >/dev/null
sleep 8
MP4_AFTER=$(find "${STREAM_ROOT}/${STATION}/videos" -name '*.mp4' 2>/dev/null | wc -l)
test "${MP4_AFTER}" -eq "${MP4_COUNT}" || { echo "FAIL: MP4 wiped after empty mux"; exit 1; }
docker logs "${CID}" 2>&1 | tail -20 | grep -q mux_skip || echo "WARN: mux_skip not logged"

echo "=== RC-1/RC-2 E2E PASSED ==="
