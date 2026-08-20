#!/usr/bin/env bash
# Run full EgoDome production acceptance (v0.1.1 + Phase D + P-Ops async).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STATION="${STATION_ID:-ego-001}"
BOOT_CHECK=0

for arg in "$@"; do
  case "$arg" in
    --boot-check) BOOT_CHECK=1 ;;
    -h|--help)
      cat <<EOF
用法: rc-ego-001-production.sh [--boot-check]

  依次运行 v0.1.1 preflight、Phase D、P-Ops async 验收。
  --boot-check  供 systemd 开机调用（失败只 WARN，不阻断启动）
EOF
      exit 0
      ;;
  esac
done

log() { echo "[production-rc] $*"; }
run_step() {
  local name="$1"
  shift
  log "=== ${name} ==="
  if "$@"; then
    log "OK: ${name}"
    return 0
  fi
  local rc=$?
  if [[ "${BOOT_CHECK}" -eq 1 ]]; then
    log "WARN: ${name} failed (boot-check, exit ${rc})"
    return 0
  fi
  log "FAIL: ${name} (exit ${rc})"
  return "${rc}"
}

FAIL=0
run_step "v0.1.1-preflight" bash "${SCRIPT_DIR}/rc-ego-001-v0.1.1-preflight.sh" || FAIL=$((FAIL + 1))
run_step "phase-d" bash "${SCRIPT_DIR}/rc-ego-001-p2-phase-d.sh" || FAIL=$((FAIL + 1))
run_step "ops-async" bash "${SCRIPT_DIR}/rc-ego-001-p2-ops-async-preflight.sh" || FAIL=$((FAIL + 1))

if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active data-lab-ego-process-watcher.timer >/dev/null 2>&1 \
    || systemctl --user is-active data-lab-ego-process-watcher.timer >/dev/null 2>&1; then
    log "OK: ego-process-watcher timer active"
  else
    log "WARN: ego-process-watcher timer not active (install with install-ego-process-watcher-systemd.sh)"
    [[ "${BOOT_CHECK}" -eq 0 ]] && FAIL=$((FAIL + 1))
  fi
fi

log "=== production-rc summary station=${STATION} fail=${FAIL} ==="
[[ "${FAIL}" -eq 0 ]]
