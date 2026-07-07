#!/usr/bin/env bash
# Batch-0 (Phase-1 hardening) acceptance for ego-lan-214 on 34.
# Usage: bash data-lab-platform/scripts/ego-lan-214-phase1-lib-acceptance.sh
set -euo pipefail

STATION="${STATION_ID:-ego-lan-214}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STREAM_HOST="${ROOT}/data-storage/stream/${STATION}"
API_BASE="${API_BASE:-http://127.0.0.1:8080}"
INGEST_CONTAINER="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
SYNC_CONTAINER="${STREAM_PARQUET_SYNC_CONTAINER:-data-lab-stream-parquet-sync-1}"
EXPECTED_SEGMENTS="${EXPECTED_SEGMENTS:-21}"
EXPECTED_FRAMES="${EXPECTED_FRAMES:-6300}"

pass=0
fail=0
warn=0

ok() { echo "[PASS] $*"; pass=$((pass + 1)); }
bad() { echo "[FAIL] $*"; fail=$((fail + 1)); }
note() { echo "[WARN] $*"; warn=$((warn + 1)); }

echo "=== ego-lan-214 Phase-1 acceptance (batch 0 + optional batch 1) ==="
echo "station: ${STATION}"
echo "stream:  ${STREAM_HOST}"
echo ""

# --- B0-3: stream-ingest env ---
for key in DERIVE_AUTO_START_ON_UPLOAD_COMPLETE DERIVE_MUX_AUTO_RETRY DERIVE_API_TWO_PHASE; do
  val="$(docker exec "${INGEST_CONTAINER}" printenv "${key}" 2>/dev/null || true)"
  if [[ "${val:-}" == "1" ]]; then
    ok "stream-ingest ${key}=${val}"
  else
    bad "stream-ingest ${key}=${val:-<unset>} (expected 1)"
  fi
done

skip="$(docker exec "${SYNC_CONTAINER}" printenv STREAM_PARQUET_SYNC_SKIP_STATIONS 2>/dev/null || true)"
if [[ "${skip}" == *"${STATION}"* ]]; then
  ok "stream-parquet-sync SKIP includes ${STATION}"
else
  bad "stream-parquet-sync SKIP='${skip:-}' missing ${STATION}"
fi

# --- B0-4: two-phase API ---
derive_json="$(curl -s --max-time 12 "${API_BASE}/lerobot/api/collection/stations/${STATION}/derive-status" || true)"
if [[ -z "${derive_json}" ]]; then
  note "derive-status API timeout — using disk fallback"
  export EGO_DATALAB_ROOT="${ROOT}"
  derive_json="$(python3 -c "
import json, os
from pathlib import Path
import sys
sys.path.insert(0, '${ROOT}/data-lab-platform/ego-stream-client')
from derive_status import load_derive_status_from_disk
d = load_derive_status_from_disk('${STATION}', Path('${ROOT}'))
print(json.dumps(d or {}))
")"
fi

phase="$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('phase',''))" <<<"${derive_json}")"
mp4_ok="$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('progress',{}).get('mp4Ok', False))" <<<"${derive_json}")"
has_eta="$(python3 -c "import json,sys; d=json.load(sys.stdin); print('etaSeconds' in d)" <<<"${derive_json}")"

if [[ "${phase}" == "READY" || "${phase}" == "UPLOADED" || "${phase}" == "IDLE" ]]; then
  ok "derive-status external phase=${phase} (two-phase)"
else
  bad "derive-status external phase=${phase} (unexpected; DERIVING should be mapped)"
fi

if [[ "${has_eta}" == "False" ]]; then
  ok "derive-status omits hardcoded etaSeconds"
else
  bad "derive-status still exposes etaSeconds"
fi

# --- Disk truth ---
raw_n="$(find "${STREAM_HOST}/raw/segments" -name '*.tar.zst' 2>/dev/null | wc -l | tr -d ' ')"
markers_n="$(find "${STREAM_HOST}/live/derive/markers" -name '*.ok.json' 2>/dev/null | wc -l | tr -d ' ')"
parquet_rows="$(python3 -c "
import json
from pathlib import Path
p=Path('${STREAM_HOST}/meta/info.json')
print(json.loads(p.read_text()).get('ingest_row_count',0) if p.is_file() else 0)
" 2>/dev/null || echo 0)"

if [[ "${raw_n}" -ge 1 ]]; then ok "raw tar.zst count=${raw_n}"; else bad "no raw segments"; fi
if [[ "${markers_n}" -ge "${EXPECTED_SEGMENTS}" || "${phase}" == "READY" ]]; then
  ok "derive markers=${markers_n}"
else
  note "derive markers=${markers_n} (expected >=${EXPECTED_SEGMENTS} when fully derived)"
fi

if [[ "${parquet_rows}" -ge $((EXPECTED_FRAMES - 10)) ]]; then
  ok "parquet rows=${parquet_rows}"
else
  note "parquet rows=${parquet_rows} (expected ~${EXPECTED_FRAMES})"
fi

# --- MP4 frames ---
CAMERAS=(
  observation.images.camera_front_left
  observation.images.camera_front_right
  observation.images.camera_rear_left
  observation.images.camera_rear_right
)
if [[ "${phase}" == "READY" || "${mp4_ok}" == "True" ]]; then
  for cam in "${CAMERAS[@]}"; do
    mp4="${STREAM_HOST}/videos/${cam}/chunk-000/file-000.mp4"
    if [[ ! -f "${mp4}" ]]; then bad "missing ${cam}"; continue; fi
    frames="$(ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=nb_read_packets -of csv=p=0 "${mp4}" 2>/dev/null || echo 0)"
    if [[ "${frames}" -ge $((EXPECTED_FRAMES - 1)) && "${frames}" -le $((EXPECTED_FRAMES + 1)) ]]; then
      ok "${cam} frames=${frames}"
    else
      bad "${cam} frames=${frames} (expected ~${EXPECTED_FRAMES})"
    fi
  done
else
  note "skip MP4 frame checks (not READY)"
fi

# --- Logs: no recent lock / emergency purge during mux ---
if docker logs "${INGEST_CONTAINER}" --since 2h 2>&1 | grep -q 'parquet\.lock.*timeout'; then
  bad "recent parquet.lock timeout in stream-ingest logs"
else
  ok "no recent parquet.lock timeout (2h)"
fi

if docker logs "${INGEST_CONTAINER}" --since 2h 2>&1 | grep 'staging_emergency_purge' | grep -q "${STATION}"; then
  note "staging_emergency_purge seen in 2h logs (review if during mux)"
else
  ok "no staging_emergency_purge for ${STATION} (2h)"
fi

# --- Optional full verify ---
if [[ "${RUN_FULL_VERIFY:-0}" == "1" ]]; then
  CHECK_DERIVE_ASYNC=1 bash "${ROOT}/data-lab-platform/scripts/ego-lan-214-reimport-verify.sh" || bad "reimport-verify failed"
fi

# --- Batch 1 env (always check) ---
extract_backend="$(docker exec "${INGEST_CONTAINER}" printenv DERIVE_EXTRACT_BACKEND 2>/dev/null || true)"
if [[ -z "${extract_backend}" || "${extract_backend}" == "legacy" ]]; then
  ok "DERIVE_EXTRACT_BACKEND=${extract_backend:-legacy} (gray default)"
else
  note "DERIVE_EXTRACT_BACKEND=${extract_backend} (non-default rollout)"
fi

verify_sha="$(docker exec "${INGEST_CONTAINER}" printenv DERIVE_EXTRACT_VERIFY_SHA256 2>/dev/null || true)"
if [[ "${verify_sha:-1}" == "1" ]]; then
  ok "DERIVE_EXTRACT_VERIFY_SHA256=1"
else
  note "DERIVE_EXTRACT_VERIFY_SHA256=${verify_sha:-<unset>}"
fi

mux_backend="$(docker exec "${INGEST_CONTAINER}" printenv DERIVE_MUX_BACKEND 2>/dev/null || true)"
if [[ -z "${mux_backend}" || "${mux_backend}" == "fluent" ]]; then
  ok "DERIVE_MUX_BACKEND=${mux_backend:-fluent} (batch-2 default)"
elif [[ "${mux_backend}" == "legacy" ]]; then
  note "DERIVE_MUX_BACKEND=legacy (rollback mode)"
else
  note "DERIVE_MUX_BACKEND=${mux_backend}"
fi

if [[ "${RUN_EXTRACT_BATCH1:-0}" == "1" ]]; then
  echo ""
  echo "--- Batch 1 extract专项 ---"
  EXTRACT_SCRIPT="/app/scripts/extract-tar-zst.py"
  if docker exec "${INGEST_CONTAINER}" test -f "${EXTRACT_SCRIPT}"; then
    ok "extract-tar-zst.py mounted"
  else
    bad "extract-tar-zst.py missing in ${INGEST_CONTAINER}"
  fi

  if docker exec "${INGEST_CONTAINER}" python3 -c "import zstandard" 2>/dev/null; then
    ok "python zstandard importable"
  else
    bad "python zstandard not installed (rebuild stream-ingest image)"
  fi

  sample_archive="$(find "${STREAM_HOST}/raw/segments" -name 'seg_*.tar.zst' 2>/dev/null | sort | head -1)"
  if [[ -z "${sample_archive}" ]]; then
    bad "no sample tar.zst for extract A/B"
  else
    sample_rel="${sample_archive#"${STREAM_HOST}"/}"
    sample_in_container="/srv/stream/${STATION}/${sample_rel}"
    work_base="/tmp/b1-extract-accept-$$"
    legacy_dir="${work_base}/legacy"
    python_dir="${work_base}/python"
    docker exec "${INGEST_CONTAINER}" rm -rf "${work_base}" 2>/dev/null || true
    docker exec "${INGEST_CONTAINER}" mkdir -p "${legacy_dir}" "${python_dir}"

    legacy_ms="$(docker exec "${INGEST_CONTAINER}" sh -c "
      start=\$(date +%s%3N)
      zstd -d -c '${sample_in_container}' | tar -x -C '${legacy_dir}'
      echo \$(( \$(date +%s%3N) - start ))
    " 2>/dev/null || echo 999999)"

    py_status=0
    py_ms="$(docker exec "${INGEST_CONTAINER}" sh -c "
      start=\$(date +%s%3N)
      python3 '${EXTRACT_SCRIPT}' '${sample_in_container}' '${python_dir}' >/dev/null || exit \$?
      echo \$(( \$(date +%s%3N) - start ))
    " 2>/dev/null)" || py_status=$?

    if [[ "${py_status}" -eq 0 ]]; then
      ok "python extract succeeded (${py_ms}ms)"
    else
      bad "python extract failed (status=${py_status})"
    fi

    diff_out="$(docker exec "${INGEST_CONTAINER}" diff -rq "${legacy_dir}" "${python_dir}" 2>&1 || true)"
    if [[ -z "${diff_out}" ]]; then
      ok "legacy vs python tree identical (sample segment)"
    else
      bad "legacy vs python diff: ${diff_out:0:200}"
    fi

    if [[ "${legacy_ms}" != "999999" && "${py_ms}" =~ ^[0-9]+$ ]]; then
      ratio=$(( py_ms * 100 / (legacy_ms + 1) ))
      if [[ "${ratio}" -le 120 ]]; then
        ok "sample extract perf python/legacy=${ratio}% (legacy=${legacy_ms}ms python=${py_ms}ms)"
      else
        note "sample extract perf python/legacy=${ratio}% exceeds 120% target"
      fi
    fi

    # Truncated archive must fail with no output dir
    trunc_dir="${work_base}/trunc"
    trunc_archive="${work_base}/trunc.tar.zst"
    docker exec "${INGEST_CONTAINER}" sh -c "head -c 4096 '${sample_in_container}' > '${trunc_archive}'"
    docker exec "${INGEST_CONTAINER}" mkdir -p "${trunc_dir}"
    if docker exec "${INGEST_CONTAINER}" python3 "${EXTRACT_SCRIPT}" "${trunc_archive}" "${trunc_dir}" 2>/dev/null; then
      bad "truncated archive should fail extract"
    else
      ok "truncated archive rejected"
    fi
    trunc_files="$(docker exec "${INGEST_CONTAINER}" find "${trunc_dir}" -type f 2>/dev/null | wc -l | tr -d ' ')"
    if [[ "${trunc_files}" == "0" ]]; then
      ok "truncated extract left no files"
    else
      bad "truncated extract left ${trunc_files} files"
    fi

    # Path traversal must be blocked
    traversal_archive="${work_base}/evil.tar.zst"
    if docker exec "${INGEST_CONTAINER}" python3 -c "
import io, tarfile, zstandard as zstd
from pathlib import Path
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode='w') as tar:
    data = tarfile.TarInfo(name='../../../tmp/b1-evil-traversal')
    data.size = 4
    tar.addfile(data, io.BytesIO(b'evil'))
cctx = zstd.ZstdCompressor()
Path('${traversal_archive}').write_bytes(cctx.compress(buf.getvalue()))
" 2>/dev/null; then
      evil_dir="${work_base}/evil-out"
      docker exec "${INGEST_CONTAINER}" mkdir -p "${evil_dir}"
      if docker exec "${INGEST_CONTAINER}" python3 "${EXTRACT_SCRIPT}" "${traversal_archive}" "${evil_dir}" >/dev/null 2>&1; then
        bad "path traversal archive should be rejected"
      else
        ok "path traversal archive rejected"
      fi
      if docker exec "${INGEST_CONTAINER}" test -f /tmp/b1-evil-traversal 2>/dev/null; then
        bad "path traversal wrote outside dest"
        docker exec "${INGEST_CONTAINER}" rm -f /tmp/b1-evil-traversal 2>/dev/null || true
      else
        ok "no traversal file outside dest"
      fi
    else
      note "skip path-traversal synthetic archive (zstandard unavailable)"
    fi

    docker exec "${INGEST_CONTAINER}" rm -rf "${work_base}" /tmp/b1-evil-traversal 2>/dev/null || true
  fi

  if [[ "${RUN_EXTRACT_ALL_SEGMENTS:-0}" == "1" && -n "${sample_archive:-}" ]]; then
    echo "--- Batch 1 full-segment A/B (21段) ---"
    seg_fail=0
    while IFS= read -r archive; do
      [[ -f "${archive}" ]] || continue
      base="$(basename "${archive}" .tar.zst)"
      rel="${archive#"${STREAM_HOST}"/}"
      in_c="/srv/stream/${STATION}/${rel}"
      ldir="${work_base}-all/${base}/legacy"
      pdir="${work_base}-all/${base}/python"
      docker exec "${INGEST_CONTAINER}" mkdir -p "${ldir}" "${pdir}"
      docker exec "${INGEST_CONTAINER}" sh -c "zstd -d -c '${in_c}' | tar -x -C '${ldir}'" >/dev/null
      docker exec "${INGEST_CONTAINER}" python3 "${EXTRACT_SCRIPT}" "${in_c}" "${pdir}" >/dev/null || seg_fail=$((seg_fail + 1))
      if ! docker exec "${INGEST_CONTAINER}" diff -rq "${ldir}" "${pdir}" >/dev/null 2>&1; then
        bad "segment mismatch ${base}"
        seg_fail=$((seg_fail + 1))
      fi
    done < <(find "${STREAM_HOST}/raw/segments" -name 'seg_*.tar.zst' 2>/dev/null | sort)
    if [[ "${seg_fail}" -eq 0 ]]; then
      ok "all segments legacy/python identical"
    fi
    docker exec "${INGEST_CONTAINER}" rm -rf "${work_base}-all" 2>/dev/null || true
  fi
fi

# --- Batch 2: mux backend (RUN_MUX_BATCH2=1) ---
if [[ "${RUN_MUX_BATCH2:-0}" == "1" ]]; then
  echo ""
  echo "--- Batch 2 mux专项 ---"
  if docker exec "${INGEST_CONTAINER}" test -f /app/mux-exec.mjs; then
    ok "mux-exec.mjs mounted"
  else
    bad "mux-exec.mjs missing"
  fi

  if docker exec "${INGEST_CONTAINER}" node -e "import('fluent-ffmpeg').then(()=>process.exit(0)).catch(()=>process.exit(1))" 2>/dev/null; then
    ok "fluent-ffmpeg importable"
  else
    bad "fluent-ffmpeg not installed (rebuild stream-ingest image)"
  fi

  sample_mp4="${STREAM_HOST}/videos/observation.images.camera_front_left/chunk-000/file-000.mp4"
  if [[ -f "${sample_mp4}" ]]; then
    legacy_frames="$(docker exec "${INGEST_CONTAINER}" node -e "
import { probeMp4FrameCount } from '/app/mux-exec.mjs';
process.env.DERIVE_MUX_BACKEND='legacy';
console.log(probeMp4FrameCount('${sample_mp4}'.replace('${STREAM_HOST}','/srv/stream/${STATION}')));
" 2>/dev/null || echo 0)"
    fluent_frames="$(docker exec "${INGEST_CONTAINER}" node -e "
import { probeMp4FrameCount } from '/app/mux-exec.mjs';
process.env.DERIVE_MUX_BACKEND='fluent';
console.log(probeMp4FrameCount('${sample_mp4}'.replace('${STREAM_HOST}','/srv/stream/${STATION}')));
" 2>/dev/null || echo 0)"
    if [[ "${legacy_frames}" == "${fluent_frames}" && "${legacy_frames}" -ge $((EXPECTED_FRAMES - 1)) ]]; then
      ok "probe frame count legacy=fluent=${legacy_frames}"
    else
      bad "probe mismatch legacy=${legacy_frames} fluent=${fluent_frames}"
    fi

    has_duration="$(docker exec "${INGEST_CONTAINER}" node -e "
import { buildMp4ConcatList } from '/app/mux-exec.mjs';
const list = buildMp4ConcatList('/a/first.mp4','/a/second.mp4',{firstDurationSec:1.5});
console.log(list.includes('duration 1.5') ? 'yes' : 'no');
" 2>/dev/null || echo no)"
    if [[ "${has_duration}" == "yes" ]]; then
      ok "fluent concat list includes duration line"
    else
      bad "fluent concat list missing duration"
    fi
  else
    note "skip mux probe (no sample MP4 — run after READY)"
  fi

  if docker exec "${INGEST_CONTAINER}" find /srv/stream/${STATION} -name '*.muxing.tmp' 2>/dev/null | grep -q .; then
    bad "mux temp files left on disk"
  else
    ok "no muxing.tmp residue on disk"
  fi
fi

video_export_backend="$(docker exec "${INGEST_CONTAINER}" printenv DERIVE_VIDEO_EXPORT_BACKEND 2>/dev/null || true)"
if [[ "${video_export_backend}" == "auto" ]]; then
  ok "DERIVE_VIDEO_EXPORT_BACKEND=auto (Phase B default)"
elif [[ "${video_export_backend}" == "staging" ]]; then
  note "DERIVE_VIDEO_EXPORT_BACKEND=staging (Phase A.1 rollback — recreate stream-ingest for auto)"
else
  note "DERIVE_VIDEO_EXPORT_BACKEND=${video_export_backend:-<unset>}"
fi

if docker exec "${INGEST_CONTAINER}" test -f /app/scripts/export-videos-from-parquet.py; then
  ok "export-videos-from-parquet.py present"
else
  bad "export-videos-from-parquet.py missing"
fi

if [[ "${RUN_PHASE_B_REGRESSION:-0}" == "1" ]]; then
  echo ""
  echo "--- Phase B regression ---"
  bash "${ROOT}/data-lab-platform/scripts/ego-lan-214-phase-b-regression.sh" || bad "phase-b-regression failed"
fi

echo ""
echo "=== summary: pass=${pass} fail=${fail} warn=${warn} ==="
if [[ "${fail}" -gt 0 ]]; then
  exit 1
fi
exit 0
