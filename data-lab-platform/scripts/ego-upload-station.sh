#!/usr/bin/env bash
# ego-upload — 130 采集站一句话上传（幂等：只传未 UPLOADED 的 closed segment）
#
# 用法: ego-upload [station] [upload_segments 额外参数…]
# 示例: ego-upload ego-001
#
# 安装: ego-130-provision.sh 会复制到 ~/.local/bin/ego-upload
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"

STATION=""
EXTRA=()
NOTIFY_FLAG=""
for arg in "$@"; do
  case "$arg" in
    -h|--help)
      cat <<'EOF'
用法: ego-upload [station] [选项]

  ego-upload ego-001     上传全站所有 session 的待传段 + 通知 34 ego-process
  ego-upload             使用 EGO_STATION_ID / ego-station.env 中的站点
  ego-upload --no-notify 仅上传，不通知 34（egodome 不会自动更新）
  ego-upload ego-001 --session-id sess_xxx   只上传指定 session（高级）

默认扫描 segments/sessions/sess_*/ 下全部 CLOSED 待传段（跨所有录制批次）。
幂等：本地已 UPLOADED 的段跳过；34 对已 commit 段返回 duplicate。
上传成功后默认删除本地段（EGO_SEGMENT_DELETE_AFTER_UPLOAD=1）。
EOF
      exit 0
      ;;
    -*)
      case "$arg" in
        --notify) NOTIFY_FLAG=1 ;;
        --no-notify) NOTIFY_FLAG=0 ;;
        *) EXTRA+=("$arg") ;;
      esac
      ;;
    *)
      if [[ -z "$STATION" ]]; then
        STATION="$arg"
      else
        EXTRA+=("$arg")
      fi
      ;;
  esac
done

set -a
# shellcheck disable=SC1090
source "${HOME}/.config/ego-station.env" 2>/dev/null || true
# shellcheck disable=SC1090
source "${HOME}/.config/ego-station.env.d/station.conf" 2>/dev/null || true
set +a

STATION="${STATION:-${EGO_STATION_ID:-${STATION_ID:-}}}"
[[ -n "$STATION" ]] || {
  echo "❌ 请指定站点，例如: ego-upload ego-001" >&2
  exit 2
}

_apply_station_upload_profile() {
  case "${STATION}" in
    ego-001)
      export EGO_STATION_ID=ego-001
      export EGO_SEGMENT_ROOT="${HOME}/cache/ego-001/segments"
      export EGO_CAPTURE_CHECKPOINT="${HOME}/cache/ego-001/checkpoint.json"
      export EGO_UPLOAD_LOG_DIR="${HOME}/cache/ego-001/logs"
      export EGO_UPLOAD_URL="http://10.10.10.34:8080/lerobot/api/collection/stations/ego-001/upload"
      export DATALAB_HEARTBEAT_URL="${EGO_UPLOAD_URL}"
      export STATION_UPLOAD_TOKEN=dl-upload-ego-001-v1
      export UPLOAD_PROTOCOL=mcap
      export SEGMENT_MCAP=1
      export SEGMENT_FRAME_BIN=0
      export DATALAB_UPLOAD_TIMEOUT_S="${DATALAB_UPLOAD_TIMEOUT_S:-900}"
      export EGO_UPLOAD_MAX_FRAMES="${EGO_UPLOAD_MAX_FRAMES:-2000}"
      export EGO_UPLOAD_MAX_FRAMES_MODE="${EGO_UPLOAD_MAX_FRAMES_MODE:-warn}"
      ;;
  esac
}

_apply_station_upload_profile

if [[ -n "$NOTIFY_FLAG" ]]; then
  NOTIFY_PROCESS="$NOTIFY_FLAG"
elif [[ "${EGO_NOTIFY_PROCESS:-1}" == "1" ]]; then
  NOTIFY_PROCESS=1
else
  NOTIFY_PROCESS=0
fi

_resolve_python() {
  local candidates=(
    "${EGO_PYTHON:-}"
    "${HOME}/workspace/ego-studio/.venv/bin/python"
    "$(command -v python3 2>/dev/null || true)"
  )
  local c
  for c in "${candidates[@]}"; do
    [[ -n "$c" && -x "$c" ]] && { echo "$c"; return 0; }
  done
  return 1
}

PY="$(_resolve_python)" || {
  echo "❌ 未找到 Python（ego-studio venv）" >&2
  exit 2
}

SEG_ROOT="${EGO_SEGMENT_ROOT:-${HOME}/cache/${STATION}/segments}"
UPLOAD_URL="${EGO_UPLOAD_URL:-${DATALAB_HEARTBEAT_URL:-http://10.10.10.34:8080/lerobot/api/collection/stations/${STATION}/upload}}"
export PYTHONPATH="${EGO_CAPTURE_SRC:-${HOME}/workspace/ego-studio/src}${PYTHONPATH:+:${PYTHONPATH}}"

LOG_DIR="${EGO_UPLOAD_LOG_DIR:-${HOME}/cache/${STATION}/logs}"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/ego-upload-$(date +%Y%m%d-%H%M%S).log"

# segment_store validates SEGMENT_MCAP / SEGMENT_FRAME_BIN at import time.
# Infer from pending on-disk segments so JPEG (tarzst) and MCAP uploads both work.
_sync_segment_store_env() {
  local mode
  mode="$(
    SEG_ROOT="$SEG_ROOT" "$PY" - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["SEG_ROOT"])
sessions = root / "sessions"
if not sessions.is_dir():
    print("unknown")
    raise SystemExit(0)

def pending_segment_dirs():
    for sess in sorted(sessions.iterdir()):
        if not sess.is_dir() or not sess.name.startswith("sess_"):
            continue
        seg_roots = [sess / "segments"]
        legacy = root / "sessions" / "segments"
        if legacy.is_dir():
            seg_roots.append(legacy)
        for seg_root in seg_roots:
            if not seg_root.is_dir():
                continue
            for seg in sorted(seg_root.iterdir()):
                if not seg.is_dir() or not seg.name.startswith("seg_"):
                    continue
                mf = seg / "manifest.json"
                if not mf.is_file():
                    continue
                try:
                    data = json.loads(mf.read_text(encoding="utf-8"))
                except Exception:
                    continue
                status = str(data.get("status") or "").upper()
                if status not in {"CLOSED", "UPLOADING", "UPLOAD_FAILED"}:
                    continue
                if status == "UPLOADED":
                    continue
                yield seg, data

for seg, data in pending_segment_dirs():
    if (seg / "segment.mcap").is_file():
        print("mcap")
        raise SystemExit(0)
    storage = str(data.get("storage_format") or "").lower()
    if storage == "dlb1" or (seg / "frames").is_dir():
        print("tarzst")
        raise SystemExit(0)

print("unknown")
PY
  )"
  case "$mode" in
    mcap)
      export UPLOAD_PROTOCOL=mcap
      export SEGMENT_MCAP=1
      unset SEGMENT_FRAME_BIN
      ;;
    tarzst)
      export UPLOAD_PROTOCOL=tarzst
      export SEGMENT_FRAME_BIN=1
      unset SEGMENT_MCAP
      ;;
    *)
      if [[ "${UPLOAD_PROTOCOL:-tarzst}" == "mcap" ]]; then
        export SEGMENT_MCAP=1
        export SEGMENT_FRAME_BIN=0
      else
        export UPLOAD_PROTOCOL="${UPLOAD_PROTOCOL:-tarzst}"
        export SEGMENT_FRAME_BIN="${SEGMENT_FRAME_BIN:-1}"
        unset SEGMENT_MCAP
      fi
      ;;
  esac
}

_sync_segment_store_env

_has_session_id_flag() {
  local a
  for a in "${EXTRA[@]}"; do
    [[ "$a" == --session-id || "$a" == --session-id=* ]] && return 0
  done
  return 1
}

_list_sessions_with_pending() {
  SEG_ROOT="$SEG_ROOT" "$PY" - <<'PY'
import os
from pathlib import Path
from ego_capture_studio.capture.segment_store import list_closed_pending_segments

root = Path(os.environ["SEG_ROOT"])
sessions = root / "sessions"
if not sessions.is_dir():
    raise SystemExit(0)
for sess in sorted(sessions.iterdir()):
    if not sess.is_dir() or not sess.name.startswith("sess_"):
        continue
    if list_closed_pending_segments(root, sess.name):
        print(sess.name)
PY
}

_check_upload_frame_budget() {
  local max_frames="${EGO_UPLOAD_MAX_FRAMES:-2000}"
  local mode="${EGO_UPLOAD_MAX_FRAMES_MODE:-warn}"
  SEG_ROOT="$SEG_ROOT" MAX_FRAMES="$max_frames" MODE="$mode" "$PY" - <<'PY' || return 1
import json
import os
import sys
from pathlib import Path
from ego_capture_studio.capture.segment_store import list_closed_pending_segments

root = Path(os.environ["SEG_ROOT"])
max_frames = int(os.environ.get("MAX_FRAMES", "2000"))
mode = os.environ.get("MODE", "warn").strip().lower()
sessions = root / "sessions"
if not sessions.is_dir():
    raise SystemExit(0)
violations = []
for sess in sorted(sessions.iterdir()):
    if not sess.is_dir() or not sess.name.startswith("sess_"):
        continue
    for seg_dir in list_closed_pending_segments(root, sess.name):
        manifest = seg_dir / "manifest.json"
        frames = 0
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                frames = int(data.get("frame_count") or data.get("frames") or 0)
            except Exception:
                frames = 0
        if frames > max_frames:
            violations.append(f"{sess.name}/{seg_dir.name}: {frames}>{max_frames}")
if violations:
    prefix = "⚠️" if mode == "warn" else "❌"
    print(f"{prefix} 待上传段超过帧数上限 ({max_frames})：", file=sys.stderr)
    for line in violations:
        print(f"   {line}", file=sys.stderr)
    print(
        f"   提示: export EGO_STRICT_EPISODE_SECONDS=35 并清 checkpoint 后重录；"
        f"或提高 EGO_UPLOAD_MAX_FRAMES",
        file=sys.stderr,
    )
    if mode != "warn":
        raise SystemExit(1)
PY
}

_preflight_mcap_segments() {
  local protocol="${UPLOAD_PROTOCOL:-}"
  [[ "${protocol}" == "mcap" ]] || return 0
  SEG_ROOT="$SEG_ROOT" "$PY" - <<'PY' || return 1
import os
import sys
from pathlib import Path

from ego_capture_studio.capture.segment_store import list_closed_pending_segments
from ego_capture_studio.capture.mcap_preflight import preflight_segment_dir_or_raise, McapPreflightError

root = Path(os.environ["SEG_ROOT"])
sessions = root / "sessions"
if not sessions.is_dir():
    raise SystemExit(0)
failures = []
for sess in sorted(sessions.iterdir()):
    if not sess.is_dir() or not sess.name.startswith("sess_"):
        continue
    for seg_dir in list_closed_pending_segments(root, sess.name):
        try:
            preflight_segment_dir_or_raise(seg_dir)
        except McapPreflightError as exc:
            failures.append(f"{sess.name}/{seg_dir.name}: {exc}")
if failures:
    print("❌ MCAP preflight 未通过，已阻断上传：", file=sys.stderr)
    for line in failures:
        print(f"   {line}", file=sys.stderr)
    raise SystemExit(1)
print("[ego-upload] mcap_preflight_ok", file=sys.stderr)
PY
}

_upload_one_session() {
  local sid="$1"
  local n start_line
  echo "[ego-upload] >>> session=${sid}" | tee -a "$LOG_FILE" >&2
  start_line=$(wc -l < "$LOG_FILE")
  start_line=$((start_line + 1))
  set +e
  "$PY" -m ego_capture_studio.cli.upload_segments \
    --session-id "$sid" \
    --limit 0 \
    --ensure-session \
    --segment-root "$SEG_ROOT" \
    --upload-url "$UPLOAD_URL" \
    "${EXTRA[@]}" 2>&1 | tee -a "$LOG_FILE" >&2
  UPLOAD_RC=${PIPESTATUS[0]}
  set -e
  n="$(sed -n "${start_line},\$p" "$LOG_FILE" | grep -E '^uploaded_segments=' | tail -1 | cut -d= -f2 || true)"
  [[ "$n" =~ ^[0-9]+$ ]] || n=0
  UPLOAD_N="$n"
}

echo "[ego-upload] station=${STATION} segment-root=${SEG_ROOT}" | tee -a "$LOG_FILE"
echo "[ego-upload] upload-url=${UPLOAD_URL}" | tee -a "$LOG_FILE"
echo "[ego-upload] upload-protocol=${UPLOAD_PROTOCOL:-tarzst}" | tee -a "$LOG_FILE"

TOTAL_UPLOADED=0
SESSIONS_OK=0
SESSIONS_FAIL=0
FAILED_SIDS=()

if _has_session_id_flag; then
  echo "[ego-upload] mode=single-session (--session-id)" | tee -a "$LOG_FILE"
  UNTIL_ARGS=()
  if [[ "${EGO_UPLOAD_UNTIL_COMPLETE:-1}" == "1" ]]; then
    UNTIL_ARGS=(--until-complete)
  fi
  set +e
  "$PY" -m ego_capture_studio.cli.upload_segments \
    --limit 0 \
    --ensure-session \
    --segment-root "$SEG_ROOT" \
    --upload-url "$UPLOAD_URL" \
    "${UNTIL_ARGS[@]}" \
    "${EXTRA[@]}" 2>&1 | tee -a "$LOG_FILE"
  rc=${PIPESTATUS[0]}
  set -e
  if [[ "$rc" -ne 0 ]]; then
    echo "❌ 上传失败，详见: ${LOG_FILE}" >&2
    exit "$rc"
  fi
  TOTAL_UPLOADED="$(grep -E '^uploaded_segments=' "$LOG_FILE" | tail -1 | cut -d= -f2 || echo 0)"
  [[ "$TOTAL_UPLOADED" =~ ^[0-9]+$ ]] || TOTAL_UPLOADED=0
else
  echo "[ego-upload] mode=all-sessions (pending CLOSED segments)" | tee -a "$LOG_FILE"
  _check_upload_frame_budget | tee -a "$LOG_FILE" >&2
  _preflight_mcap_segments | tee -a "$LOG_FILE" >&2
  mapfile -t PENDING_SESSIONS < <(_list_sessions_with_pending)
  if [[ ${#PENDING_SESSIONS[@]} -eq 0 ]]; then
    echo "无待传段（所有 session 均已 UPLOADED 或尚无 closed 段）" | tee -a "$LOG_FILE"
  else
    echo "[ego-upload] sessions_with_pending=${#PENDING_SESSIONS[@]}: ${PENDING_SESSIONS[*]}" | tee -a "$LOG_FILE"
    for sid in "${PENDING_SESSIONS[@]}"; do
      [[ -n "$sid" ]] || continue
      _upload_one_session "$sid"
      if [[ "${UPLOAD_RC:-1}" -eq 0 ]]; then
        SESSIONS_OK=$((SESSIONS_OK + 1))
        TOTAL_UPLOADED=$((TOTAL_UPLOADED + ${UPLOAD_N:-0}))
      else
        SESSIONS_FAIL=$((SESSIONS_FAIL + 1))
        FAILED_SIDS+=("$sid")
        echo "❌ session ${sid} 上传失败 (exit ${UPLOAD_RC})" | tee -a "$LOG_FILE" >&2
      fi
    done
  fi
  if [[ "$SESSIONS_FAIL" -gt 0 ]]; then
    echo "❌ ${SESSIONS_FAIL} 个 session 上传失败: ${FAILED_SIDS[*]} · 日志: ${LOG_FILE}" >&2
    exit 1
  fi
fi

if [[ "${EGO_SEGMENT_DELETE_AFTER_UPLOAD:-1}" == "1" ]]; then
  PURGE_LEFT="$(
    SEG_ROOT="$SEG_ROOT" "$PY" - <<'PY'
import os
from pathlib import Path
from ego_capture_studio.capture.segment_store import purge_all_uploaded_segments

root = Path(os.environ["SEG_ROOT"])
remaining = purge_all_uploaded_segments(root, strict=True)
print(remaining)
PY
  )" || {
    echo "❌ 上传后本地段清理失败（仍有 UPLOADED 段残留）· 日志: ${LOG_FILE}" >&2
    exit 1
  }
  if [[ "${PURGE_LEFT:-0}" -gt 0 ]]; then
    echo "[ego-upload] purged_uploaded_segments=${PURGE_LEFT}" | tee -a "$LOG_FILE"
  fi
fi

echo "✅ 上传完成 · station=${STATION} · sessions_ok=${SESSIONS_OK:-1} · uploaded_segments=${TOTAL_UPLOADED} · 日志: ${LOG_FILE}"

if [[ "$NOTIFY_PROCESS" -eq 1 ]]; then
  NOTIFY_URL="${EGO_PROCESS_NOTIFY_URL:-${EGO_UPLOAD_URL%/upload}/process-notify}"
  TOKEN="${STATION_UPLOAD_TOKEN:-}"
  if [[ "${TOTAL_UPLOADED:-0}" -le 0 ]]; then
    :
  elif [[ -n "$TOKEN" ]] && command -v curl >/dev/null 2>&1; then
    echo "[ego-upload] 通知 34 排队 ego-process: ${NOTIFY_URL}"
    if curl -sf -X POST "${NOTIFY_URL}" -H "X-Station-Token: ${TOKEN}" -H "Content-Type: application/json" -d '{}' >/dev/null; then
      echo "   34 已收到 process-notify（需 ego-process-watcher 或手动 ego-process）"
    else
      echo "   ⚠️  process-notify 失败，请手动在 34 执行: ego-process ${STATION}" >&2
    fi
  else
    echo "   ⚠️  跳过 notify（缺少 STATION_UPLOAD_TOKEN 或 curl）" >&2
  fi
else
  echo "   已跳过 process-notify（egodome 需手动在 34 执行: ego-process ${STATION}）"
fi
