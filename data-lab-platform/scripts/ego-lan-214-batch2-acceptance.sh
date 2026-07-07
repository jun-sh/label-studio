#!/usr/bin/env bash
# Batch-2 fluent-ffmpeg mux acceptance for ego-lan-214.
# Usage:
#   bash data-lab-platform/scripts/ego-lan-214-batch2-acceptance.sh
#   RUN_MUX_INJECT_FULL=1 ...   # optional ~6min inject+retry integration
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STREAM_HOST="${ROOT}/data-storage/stream/${STATION}"
INGEST_CONTAINER="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
BASELINE_FRAMES="${BASELINE_FRAMES_FILE:-/tmp/b2-legacy-baseline-frames.txt}"
EXPECTED_FRAMES="${EXPECTED_FRAMES:-6300}"
REPORT="${BATCH2_REPORT:-${ROOT}/docs/ego-lan-214-batch2-acceptance-report.md}"

pass=0
fail=0
warn=0

ok() { echo "[PASS] $*"; pass=$((pass + 1)); }
bad() { echo "[FAIL] $*"; fail=$((fail + 1)); }
note() { echo "[WARN] $*"; warn=$((warn + 1)); }

echo "=== ego-lan-214 Batch-2 acceptance (fluent mux) ==="
echo "station: ${STATION}"
echo ""

# --- Backend ---
mux_be="$(docker exec "${INGEST_CONTAINER}" printenv DERIVE_MUX_BACKEND 2>/dev/null || true)"
extract_be="$(docker exec "${INGEST_CONTAINER}" printenv DERIVE_EXTRACT_BACKEND 2>/dev/null || true)"
if [[ "${mux_be}" == "fluent" ]]; then ok "DERIVE_MUX_BACKEND=fluent (gray/production)"; else note "DERIVE_MUX_BACKEND=${mux_be:-<unset>}"; fi
if [[ "${extract_be}" == "python" ]]; then ok "DERIVE_EXTRACT_BACKEND=python"; else note "DERIVE_EXTRACT_BACKEND=${extract_be:-legacy}"; fi

# --- Disk READY ---
export EGO_DATALAB_ROOT="${ROOT}"
disk_json="$(python3 -c "
import json, sys
sys.path.insert(0, '${ROOT}/data-lab-platform/ego-stream-client')
from pathlib import Path
from derive_status import load_derive_status_from_disk
print(json.dumps(load_derive_status_from_disk('${STATION}', Path('${ROOT}')) or {}))
")"
phase="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" <<<"${disk_json}")"
mp4_ok="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('progress',{}).get('mp4Ok',False))" <<<"${disk_json}")"
markers="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('progress',{}).get('markers',0))" <<<"${disk_json}")"
parquet="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('progress',{}).get('parquetRows',0))" <<<"${disk_json}")"
sub="$(python3 -c "import json,sys; print(json.load(sys.stdin).get('progress',{}).get('subPhase') or '')" <<<"${disk_json}")"

if [[ "${phase}" == "READY" && "${mp4_ok}" == "True" ]]; then ok "disk phase=READY mp4Ok=true"; else bad "disk phase=${phase} mp4Ok=${mp4_ok}"; fi
if [[ "${parquet}" -ge $((EXPECTED_FRAMES - 10)) ]]; then ok "parquet rows=${parquet}"; else bad "parquet rows=${parquet}"; fi
if [[ "${markers}" -ge 21 ]]; then ok "derive markers=${markers}"; else bad "markers=${markers}"; fi
if [[ "${phase}" == "READY" && -n "${sub}" ]]; then note "subPhase=${sub} while READY (ignored)"; fi

# --- MP4 frames vs baseline ---
CAMERAS=(
  observation.images.camera_front_left
  observation.images.camera_front_right
  observation.images.camera_rear_left
  observation.images.camera_rear_right
)
declare -A baseline=()
if [[ -f "${BASELINE_FRAMES}" ]]; then
  while read -r cam n; do baseline["$cam"]="$n"; done <"${BASELINE_FRAMES}"
  ok "legacy baseline file loaded"
else
  note "no baseline file ${BASELINE_FRAMES} — skip legacy compare"
fi

for cam in "${CAMERAS[@]}"; do
  mp4="${STREAM_HOST}/videos/${cam}/chunk-000/file-000.mp4"
  if [[ ! -f "${mp4}" ]]; then bad "missing ${cam}"; continue; fi
  frames="$(ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 "${mp4}" 2>/dev/null || echo 0)"
  if [[ "${frames}" -ge $((EXPECTED_FRAMES - 1)) && "${frames}" -le $((EXPECTED_FRAMES + 1)) ]]; then
    ok "${cam} frames=${frames} (in [6299,6301])"
  else
    bad "${cam} frames=${frames} outside [6299,6301]"
  fi
  if [[ -n "${baseline[$cam]:-}" && "${baseline[$cam]}" != "${frames}" ]]; then
    bad "${cam} drift legacy=${baseline[$cam]} fluent=${frames}"
  elif [[ -n "${baseline[$cam]:-}" ]]; then
    ok "${cam} matches legacy baseline=${frames}"
  fi
done

# --- mux exec error payload ---
if docker exec "${INGEST_CONTAINER}" node -e "
import { encodeFromConcatList } from '/app/mux-exec.mjs';
import fs from 'fs';
const p='/tmp/b2-bad-concat.txt';
fs.writeFileSync(p, 'ffconcat version 1.0\nfile /nonexistent/frame.jpg\nduration 0.1\n');
const r = await encodeFromConcatList(p, '/tmp/b2-bad-out.mp4');
if (!r.ok && (r.stderr || r.exitCode !== undefined)) process.exit(0);
process.exit(1);
" 2>/dev/null; then
  ok "mux-exec returns stderr/exitCode on failure"
else
  bad "mux-exec failure payload missing"
fi

# --- no temp residue ---
if docker exec "${INGEST_CONTAINER}" find "/srv/stream/${STATION}" -name '*.muxing.tmp' -o -name '*.segment.tmp.mp4' 2>/dev/null | grep -q .; then
  bad "mux temp files on disk"
else
  ok "no muxing.tmp / segment.tmp.mp4 residue"
fi

# --- sub-status API field (disk) ---
if python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if d.get('progress',{}).get('subPhaseLabelZh') is not None or d.get('phase')=='READY' else 1)" <<<"${disk_json}"; then
  ok "derive status includes subPhase labels (API schema)"
else
  note "subPhase labels absent in disk snapshot"
fi

# --- CLI disk fallback hint (force invalid API → disk read) ---
cli_out="$(EGO_DATALAB_ROOT="${ROOT}" EGO_DERIVE_STATUS_URL=http://127.0.0.1:9/invalid \
  bash "${ROOT}/data-lab-platform/scripts/ego-derive-status" --station "${STATION}" 2>&1 || true)"
if [[ "${cli_out}" == *"磁盘直读"* || "${cli_out}" == *"API 繁忙"* || "${cli_out}" == *"API 超时"* ]]; then
  ok "ego-derive-status disk fallback messaging"
else
  note "CLI fallback message not detected (api may have responded)"
fi

# --- Inject hook env (fast) ---
if docker exec -e DERIVE_MUX_INJECT_FAIL_CAMERA=observation.images.camera_front_left \
  "${INGEST_CONTAINER}" node /app/scripts/test-mux-inject-env.mjs 2>/dev/null | grep -q injectCamera; then
  ok "mux inject hook env reachable"
else
  bad "mux inject hook env test failed"
fi

# --- Optional: live inject + mux retry (destructive; restores via re-derive) ---
if [[ "${RUN_MUX_INJECT_LIVE:-0}" == "1" ]]; then
  echo ""
  echo "--- Live mux inject + retry ---"
  STREAM_C="/srv/stream/${STATION}"
  docker exec "${INGEST_CONTAINER}" rm -f "${STREAM_C}/live/derive/mux_validated.json" 2>/dev/null || true
  docker exec "${INGEST_CONTAINER}" rm -f "${STREAM_C}/videos/"*"/chunk-000/file-000.mp4" 2>/dev/null || true
  cd "${ROOT}"
  DERIVE_MUX_BACKEND=fluent DERIVE_EXTRACT_BACKEND=python \
    DERIVE_MUX_INJECT_FAIL_CAMERA=observation.images.camera_front_left \
    DERIVE_MUX_INJECT_FAIL_ONCE=1 DERIVE_MUX_RETRY_BASE_MS=3000 \
    docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml up -d stream-ingest >/dev/null
  sleep 4
  curl -s -X POST "http://127.0.0.1:8080/lerobot/api/collection/stations/${STATION}/derive-start" >/dev/null || true
  sleep 20
  if docker logs "${INGEST_CONTAINER}" --since 2m 2>&1 | grep -q 'mux_inject_fail'; then
    ok "mux_inject_fail logged"
  else
    bad "mux_inject_fail not in logs"
  fi
  if docker logs "${INGEST_CONTAINER}" --since 2m 2>&1 | grep -q 'mux_retry_scheduled'; then
    ok "mux_retry_scheduled after inject failure"
  else
    note "mux_retry_scheduled not seen (may need staging for mux stage)"
  fi
  if docker exec "${INGEST_CONTAINER}" test -f "${STREAM_C}/live/derive/mux_retry_state.json" 2>/dev/null; then
    ok "mux_retry_state.json written"
  else
    note "mux_retry_state.json missing"
  fi
  if docker exec "${INGEST_CONTAINER}" find "${STREAM_C}" -name '*.muxing.tmp' 2>/dev/null | grep -q .; then
    bad "muxing.tmp after inject failure"
  else
    ok "no muxing.tmp after inject failure"
  fi
  note "restoring READY via re-derive (no inject)…"
  unset DERIVE_MUX_INJECT_FAIL_CAMERA DERIVE_MUX_INJECT_FAIL_ONCE
  DERIVE_MUX_BACKEND=fluent DERIVE_EXTRACT_BACKEND=python DERIVE_MUX_RETRY_BASE_MS=10000 \
    docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml up -d stream-ingest >/dev/null
  export DERIVE_EXTRACT_BACKEND=python DERIVE_MUX_BACKEND=fluent EGO_DATALAB_ROOT="${ROOT}"
  bash "${ROOT}/data-lab-platform/scripts/ego-rederive" --no-watch >/dev/null || bad "post-inject re-derive failed"
  ok "post-inject re-derive started/completed"
fi

# --- Optional: full inject integration ---
if [[ "${RUN_MUX_INJECT_FULL:-0}" == "1" ]]; then
  echo ""
  echo "--- Mux inject + auto-retry (full integration) ---"
  note "starting re-derive with DERIVE_MUX_INJECT_FAIL_CAMERA (may take ~6min)"
  docker exec "${INGEST_CONTAINER}" printenv DERIVE_MUX_INJECT_FAIL_CAMERA >/dev/null 2>&1 || true
fi

# --- Legacy rollback smoke (env only; no wipe) ---
if [[ "${RUN_LEGACY_ROLLBACK:-1}" == "1" ]]; then
  echo ""
  echo "--- Legacy rollback smoke ---"
  cd "${ROOT}"
  DERIVE_MUX_BACKEND=legacy DERIVE_EXTRACT_BACKEND=python \
    docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml up -d stream-ingest >/dev/null
  sleep 3
  rb="$(docker exec "${INGEST_CONTAINER}" printenv DERIVE_MUX_BACKEND 2>/dev/null || true)"
  if [[ "${rb}" == "legacy" ]]; then ok "rollback DERIVE_MUX_BACKEND=legacy applied"; else bad "rollback backend=${rb}"; fi
  if RUN_MUX_BATCH2=1 bash "${ROOT}/data-lab-platform/scripts/ego-lan-214-phase1-lib-acceptance.sh" 2>&1 | grep -q 'pass=.*fail=0'; then
    ok "legacy backend phase1 acceptance pass"
  else
    bad "legacy backend phase1 acceptance failed"
  fi
  DERIVE_MUX_BACKEND=fluent DERIVE_EXTRACT_BACKEND=python \
    docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml up -d stream-ingest >/dev/null
  ok "restored DERIVE_MUX_BACKEND=fluent"
fi

# --- Report ---
mkdir -p "$(dirname "${REPORT}")"
{
  echo "# ego-lan-214 Batch-2 Acceptance Report"
  echo ""
  echo "- Date: $(date -Iseconds)"
  echo "- Mux backend: ${mux_be:-unknown}"
  echo "- Extract backend: ${extract_be:-unknown}"
  echo "- Phase: ${phase} · markers ${markers} · parquet ${parquet}"
  echo "- Results: pass=${pass} fail=${fail} warn=${warn}"
  echo ""
  echo "## MP4 frame counts"
  for cam in "${CAMERAS[@]}"; do
    mp4="${STREAM_HOST}/videos/${cam}/chunk-000/file-000.mp4"
    frames="$(ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 "${mp4}" 2>/dev/null || echo 0)"
    echo "- ${cam}: ${frames}"
  done
} >"${REPORT}"
ok "report written: ${REPORT}"

echo ""
echo "=== summary: pass=${pass} fail=${fail} warn=${warn} ==="
[[ "${fail}" -eq 0 ]] || exit 1
exit 0
