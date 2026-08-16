#!/usr/bin/env bash
# Switch ego-130 between production (manual upload) and debug (auto upload loop).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DROPIN_SRC="${ROOT}/ego-stream-client/systemd/ecs-upload-segments-loop.service.d"
USER_UNIT_DIR="${HOME}/.config/systemd/user"
DROPIN_DIR="${USER_UNIT_DIR}/ecs-upload-segments-loop.service.d"
ENV_SNIPPET="${HOME}/.config/ego-station.env.d/upload-mode.conf"
LOOP_UNIT="ecs-upload-segments-loop.service"

_usage() {
  cat <<EOF
Usage: $(basename "$0") production|debug|status

  production  Stop/mask auto upload loop; manual upload_segments only (default).
  debug       Enable ecs-upload-segments-loop with EGO_UPLOAD_MODE=debug.
  status      Show mode file, systemd unit, and running loop processes.
EOF
}

_install_dropin() {
  local name="$1"
  mkdir -p "${DROPIN_DIR}"
  rm -f "${DROPIN_DIR}/production-manual-only.conf" "${DROPIN_DIR}/debug-auto-upload.conf"
  install -m 0644 "${DROPIN_SRC}/${name}" "${DROPIN_DIR}/${name}"
}

_write_mode_env() {
  local mode="$1"
  mkdir -p "$(dirname "${ENV_SNIPPET}")"
  printf 'EGO_UPLOAD_MODE=%s\n' "${mode}" > "${ENV_SNIPPET}"
}

_kill_orphan_loops() {
  pkill -f '[u]pload_segments_loop.py' 2>/dev/null || true
}

set_production() {
  systemctl --user stop "${LOOP_UNIT}" 2>/dev/null || true
  systemctl --user disable "${LOOP_UNIT}" 2>/dev/null || true
  systemctl --user mask "${LOOP_UNIT}" 2>/dev/null || true
  _kill_orphan_loops
  _install_dropin "production-manual-only.conf"
  _write_mode_env "production"
  systemctl --user daemon-reload
  echo "OK: production — manual upload_segments only; loop stopped and masked."
}

set_debug() {
  systemctl --user unmask "${LOOP_UNIT}" 2>/dev/null || true
  _install_dropin "debug-auto-upload.conf"
  _write_mode_env "debug"
  systemctl --user daemon-reload
  systemctl --user enable --now "${LOOP_UNIT}"
  echo "OK: debug — ecs-upload-segments-loop enabled (EGO_UPLOAD_MODE=debug)."
}

show_status() {
  echo "=== upload mode ==="
  if [[ -f "${ENV_SNIPPET}" ]]; then
    cat "${ENV_SNIPPET}"
  else
    echo "EGO_UPLOAD_MODE=(unset, defaults to production in loop)"
  fi
  echo
  echo "=== drop-ins ==="
  ls -la "${DROPIN_DIR}" 2>/dev/null || echo "(no user drop-ins)"
  echo
  echo "=== systemd ${LOOP_UNIT} ==="
  systemctl --user is-enabled "${LOOP_UNIT}" 2>/dev/null || echo "not enabled"
  systemctl --user is-active "${LOOP_UNIT}" 2>/dev/null || echo "not active"
  systemctl --user show "${LOOP_UNIT}" -p LoadState,UnitFileState,ActiveState 2>/dev/null || true
  echo
  echo "=== processes ==="
  pgrep -af 'upload_segments_loop.py' || echo "(no loop process)"
}

MODE="${1:-}"
case "${MODE}" in
  production) set_production ;;
  debug) set_debug ;;
  status) show_status ;;
  -h|--help|"") _usage; [[ -n "${MODE}" ]] || exit 1 ;;
  *)
    echo "Unknown mode: ${MODE}" >&2
    _usage
    exit 1
    ;;
esac
