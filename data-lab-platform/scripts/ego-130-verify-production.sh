#!/usr/bin/env bash
# Verify 130 ego-standard JPEG production capture path.
# Usage:
#   ego-130-verify-production.sh [ssh_target]
#   ego-130-verify-production.sh --profile mcap-pilot [ssh_target]
set -euo pipefail

PROFILE="production"
TARGET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="${2:-}"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
用法: ego-130-verify-production.sh [--profile production|mcap-pilot|mcap-production] [ssh_target]

  production (默认)  校验 ego-001 JPEG tar.zst 生产 unit
  mcap-pilot         校验 ecs-record-oak-mcap-pilot（不触碰生产 stream unit）
EOF
      exit 0
      ;;
    *)
      TARGET="$1"
      shift
      ;;
  esac
done

TARGET="${TARGET:-server@10.10.10.130}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

case "$PROFILE" in
  mcap-pilot|mcap-production)
    exec bash "${SCRIPT_DIR}/ego-130-verify-mcap-pilot.sh" "${TARGET}"
    ;;
  production)
    ;;
  *)
    echo "未知 profile: ${PROFILE}" >&2
    exit 2
    ;;
esac

echo "==> JPEG capture path on ${TARGET}"