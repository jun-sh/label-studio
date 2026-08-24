#!/usr/bin/env bash
# ego-upload — 130 采集站一句话上传（幂等：只传未 UPLOADED 的 closed segment）
#
# 用法: ego-upload [station] [upload_segments 额外参数…]
# 示例: ego-upload ego-001
#
# 安装: ego-130-provision.sh 会复制到 ~/.local/bin/ego-upload
set -euo pipefail

STATION=""
EXTRA=()
NOTIFY_FLAG=""
for arg in "$@"; do
  case "$arg" in
    -h|--help)
      cat <<'EOF'
用法: ego-upload [station] [选项]

  ego-upload ego-001     上传 + 默认通知 34 跑 ego-process（Collection + egodome）
  ego-upload             使用 EGO_STATION_ID / ego-station.env 中的站点
  ego-upload --no-notify 仅上传，不通知 34（egodome 不会自动更新）

幂等：本地已 UPLOADED 的段跳过；34 对已 commit 段返回 duplicate。
上传成功后默认删除本地段（EGO_SEGMENT_DELETE_AFTER_UPLOAD=1）。

等价于:
  upload_segments --limit 0 --ensure-session --segment-root … --upload-url …
  + POST …/process-notify（默认开启，34 watcher 约 30s 内 ego-process）
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

echo "[ego-upload] station=${STATION} segment-root=${SEG_ROOT}" | tee -a "$LOG_FILE"
echo "[ego-upload] upload-url=${UPLOAD_URL}" >>"$LOG_FILE"

set +e
"$PY" -m ego_capture_studio.cli.upload_segments \
  --limit 0 \
  --ensure-session \
  --segment-root "$SEG_ROOT" \
  --upload-url "$UPLOAD_URL" \
  "${EXTRA[@]}" 2>&1 | tee -a "$LOG_FILE"
rc=${PIPESTATUS[0]}
set -e

if [[ "$rc" -ne 0 ]]; then
  echo "❌ 上传失败，详见: ${LOG_FILE}" >&2
  exit "$rc"
fi

echo "✅ 上传完成 · station=${STATION} · 日志: ${LOG_FILE}"

if [[ "$NOTIFY_PROCESS" -eq 1 ]]; then
  NOTIFY_URL="${EGO_PROCESS_NOTIFY_URL:-${EGO_UPLOAD_URL%/upload}/process-notify}"
  TOKEN="${STATION_UPLOAD_TOKEN:-}"
  if [[ -n "$TOKEN" ]] && command -v curl >/dev/null 2>&1; then
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
