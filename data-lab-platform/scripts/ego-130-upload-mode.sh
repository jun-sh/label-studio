#!/usr/bin/env bash
# Switch ego-130 between production (manual upload) and debug (auto upload loop).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOOP_DROPIN_SRC="${ROOT}/ego-stream-client/systemd/ecs-upload-segments-loop.service.d"
CAPTURE_DROPIN_SRC="${ROOT}/ego-stream-client/systemd/ecs-record-oak-mcap.service.d"
USER_UNIT_DIR="${HOME}/.config/systemd/user"
LOOP_DROPIN_DIR="${USER_UNIT_DIR}/ecs-upload-segments-loop.service.d"
CAPTURE_DROPIN_DIR="${USER_UNIT_DIR}/ecs-record-oak-mcap.service.d"
ENV_SNIPPET="${HOME}/.config/ego-station.env.d/upload-mode.conf"
LOOP_UNIT="ecs-upload-segments-loop.service"
INLINE_CAPTURE_DROPIN="z-mcap-inline-upload.conf"

_usage() {
  cat <<EOF
Usage: $(basename "$0") production|debug|status

  production  Stop/mask auto upload loop; record-then-upload (no upload-pending backpressure).
  debug       Enable ecs-upload-segments-loop + inline-upload backpressure (lab/RC only).
  status      Show mode file, systemd unit, and running loop processes.
EOF
}

_install_loop_dropin() {
  local name="$1"
  mkdir -p "${LOOP_DROPIN_DIR}"
  rm -f "${LOOP_DROPIN_DIR}/production-manual-only.conf" "${LOOP_DROPIN_DIR}/debug-auto-upload.conf"
  install -m 0644 "${LOOP_DROPIN_SRC}/${name}" "${LOOP_DROPIN_DIR}/${name}"
}

_write_mode_env() {
  local mode="$1"
  mkdir -p "$(dirname "${ENV_SNIPPET}")"
  printf 'EGO_UPLOAD_MODE=%s\n' "${mode}" > "${ENV_SNIPPET}"
}

_kill_orphan_loops() {
  pkill -f '[u]pload_segments_loop.py' 2>/dev/null || true
}

_set_inline_capture_backpressure() {
  local enable="$1"
  mkdir -p "${CAPTURE_DROPIN_DIR}"
  if [[ "${enable}" == "1" ]]; then
    install -m 0644 "${CAPTURE_DROPIN_SRC}/${INLINE_CAPTURE_DROPIN}" \
      "${CAPTURE_DROPIN_DIR}/${INLINE_CAPTURE_DROPIN}"
  else
    rm -f "${CAPTURE_DROPIN_DIR}/${INLINE_CAPTURE_DROPIN}"
  fi
}

set_production() {
  systemctl --user stop "${LOOP_UNIT}" 2>/dev/null || true
  systemctl --user disable "${LOOP_UNIT}" 2>/dev/null || true
  systemctl --user mask "${LOOP_UNIT}" 2>/dev/null || true
  _kill_orphan_loops
  _install_loop_dropin "production-manual-only.conf"
  _set_inline_capture_backpressure 0
  _write_mode_env "production"
  systemctl --user daemon-reload
  echo "OK: production — record-then-upload; upload loop masked; capture backpressure off."
}

set_debug() {
  systemctl --user unmask "${LOOP_UNIT}" 2>/dev/null || true
  _install_loop_dropin "debug-auto-upload.conf"
  _set_inline_capture_backpressure 1
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
  echo "=== capture inline-upload drop-in ==="
  ls -la "${CAPTURE_DROPIN_DIR}/${INLINE_CAPTURE_DROPIN}" 2>/dev/null || echo "(absent — production record-then-upload)"
  echo
  echo "=== drop-ins ==="
  ls -la "${LOOP_DROPIN_DIR}" 2>/dev/null || echo "(no user drop-ins)"
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
