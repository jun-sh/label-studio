#!/usr/bin/env bash
# Install ego-run-pipeline (34) and optionally ego-export (214) into ~/.local/bin
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
mkdir -p "$BIN_DIR"

install -m 0755 "${SCRIPT_DIR}/ego-run-pipeline" "${BIN_DIR}/ego-run-pipeline"
install -m 0755 "${SCRIPT_DIR}/ego-pipeline-sessions.py" "${BIN_DIR}/ego-pipeline-sessions.py"
ln -sf "${SCRIPT_DIR}/ego-lan-214-wait-ready-for-postprocess.sh" "${BIN_DIR}/ego-lan-214-wait-ready-for-postprocess.sh" 2>/dev/null || true

EGO_EXPORT_SRC="${SCRIPT_DIR}/../ego-local-web/scripts/ego-export"
if [[ -f "$EGO_EXPORT_SRC" ]]; then
  install -m 0755 "$EGO_EXPORT_SRC" "${BIN_DIR}/ego-export"
fi

case ":${PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *)
    echo "提示: 将以下行加入 ~/.bashrc 以便任意目录执行命令："
    echo "  export PATH=\"\${HOME}/.local/bin:\${PATH}\""
    ;;
esac

echo "已安装:"
echo "  ${BIN_DIR}/ego-run-pipeline"
[[ -f "${BIN_DIR}/ego-export" ]] && echo "  ${BIN_DIR}/ego-export"
echo ""
echo "34 平台: ego-run-pipeline ego-lan-214"
echo "214 边缘: ego-export"
