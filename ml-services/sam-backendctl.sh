#!/usr/bin/env bash
# Opt-in control plane for Data Lab SAM 2.1 ML backend (host GPU, :9090).
#
# Policy: SAM does NOT start at boot unless you run `enable-boot`.
#
# Usage:
#   bash ml-services/sam-backendctl.sh status
#   bash ml-services/sam-backendctl.sh start
#   bash ml-services/sam-backendctl.sh stop
#   bash ml-services/sam-backendctl.sh restart
#   bash ml-services/sam-backendctl.sh install          # systemd unit only (no boot, no start)
#   bash ml-services/sam-backendctl.sh enable-boot        # systemd enable (still off until start)
#   bash ml-services/sam-backendctl.sh disable-boot       # disable + stop, release GPU
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
UNIT_NAME="data-lab-sam-backend.service"
ENV_FILE="$SCRIPT_DIR/.env.sam"
PORT=9090

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
}

load_port() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    PORT="${PORT:-9090}"
  fi
}

system_unit_installed() {
  [[ -f "/etc/systemd/system/$UNIT_NAME" ]]
}

user_unit_installed() {
  [[ -f "$HOME/.config/systemd/user/$UNIT_NAME" ]]
}

run_systemctl() {
  local scope="$1"
  shift
  if [[ "$scope" == user ]]; then
    systemctl --user "$@"
    return
  fi
  if systemctl "$@" 2>/dev/null; then
    return 0
  fi
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl "$@"
  else
    sudo systemctl "$@"
  fi
}

systemctl_quiet() {
  local scope="$1"
  shift
  if [[ "$scope" == user ]]; then
    systemctl --user "$@" >/dev/null 2>&1
    return
  fi
  if systemctl "$@" >/dev/null 2>&1; then
    return 0
  fi
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl "$@" >/dev/null 2>&1
  else
    sudo systemctl "$@" >/dev/null 2>&1
  fi
}

health_url() {
  echo "http://127.0.0.1:${PORT}/health"
}

http_health() {
  curl -fsS "$(health_url)" 2>/dev/null || return 1
}

listener_pid() {
  if command -v ss >/dev/null 2>&1; then
    ss -tlnp "sport = :$PORT" 2>/dev/null \
      | awk '/pid=/ {match($0,/pid=([0-9]+)/,a); print a[1]; exit}'
  fi
}

cmd_status() {
  load_port
  echo "SAM ML backend (port $PORT)"
  echo "  Repo: $REPO_ROOT"
  if system_unit_installed; then
    echo "  Systemd unit: installed (/etc/systemd/system/$UNIT_NAME)"
    if systemctl_quiet system is-enabled "$UNIT_NAME"; then
      echo "  Boot: enabled"
    else
      echo "  Boot: disabled (default — no GPU at reboot)"
    fi
    if systemctl_quiet system is-active "$UNIT_NAME"; then
      echo "  Service: active"
    else
      echo "  Service: inactive"
    fi
  elif user_unit_installed; then
    echo "  Systemd user unit: installed"
    systemctl --user is-enabled "$UNIT_NAME" 2>/dev/null || echo "  Boot: disabled"
    systemctl --user is-active "$UNIT_NAME" 2>/dev/null || echo "  Service: inactive"
  else
    echo "  Systemd: not installed (use: bash ml-services/sam-backendctl.sh install)"
  fi

  local pid
  pid="$(listener_pid || true)"
  if [[ -n "${pid:-}" ]]; then
    echo "  Listener pid: $pid"
  fi
  if http_health >/dev/null; then
    echo "  Health: UP ($(health_url))"
  else
    echo "  Health: DOWN"
  fi

  if command -v nvidia-smi >/dev/null 2>&1; then
    local gpu_line
    gpu_line="$(nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory --format=csv,noheader 2>/dev/null \
      | grep -E 'segment_anything|_wsgi|python' || true)"
    if [[ -n "$gpu_line" ]]; then
      echo "  GPU:"
      echo "$gpu_line" | sed 's/^/    /'
    else
      echo "  GPU: no SAM python process reported"
    fi
  fi
}

cmd_start() {
  load_port
  if system_unit_installed; then
    echo "Starting via systemd ($UNIT_NAME)..."
    run_systemctl system start "$UNIT_NAME"
    sleep 2
    cmd_status
    return 0
  fi
  if user_unit_installed; then
    echo "Starting via user systemd..."
    systemctl --user start "$UNIT_NAME"
    sleep 2
    cmd_status
    return 0
  fi
  bash "$SCRIPT_DIR/start-sam-backend.sh"
}

cmd_stop() {
  if system_unit_installed; then
    if systemctl_quiet system is-active "$UNIT_NAME"; then
      echo "Stopping systemd service $UNIT_NAME..."
      run_systemctl system stop "$UNIT_NAME"
      cmd_status
      return 0
    fi
  fi
  if user_unit_installed; then
    if systemctl --user is-active "$UNIT_NAME" >/dev/null 2>&1; then
      systemctl --user stop "$UNIT_NAME"
      cmd_status
      return 0
    fi
  fi
  bash "$SCRIPT_DIR/stop-sam-backend.sh"
  cmd_status
}

cmd_restart() {
  cmd_stop
  cmd_start
}

cmd_install() {
  bash "$SCRIPT_DIR/install-sam-systemd.sh" "${1:-}"
}

cmd_enable_boot() {
  local scope="${1:-system}"
  if [[ "$scope" == "user" ]]; then
    if user_unit_installed; then
      systemctl --user enable "$UNIT_NAME"
      echo "Boot enabled (user): $UNIT_NAME"
      return 0
    fi
    echo "Install user unit first: bash ml-services/install-sam-systemd.sh --user" >&2
    exit 1
  fi
  if system_unit_installed; then
    run_systemctl system enable "$UNIT_NAME"
    echo "Boot enabled (system): $UNIT_NAME (start manually: sam-backendctl start)"
    return 0
  fi
  if user_unit_installed; then
    systemctl --user enable "$UNIT_NAME"
    echo "Boot enabled (user): $UNIT_NAME"
    return 0
  fi
  echo "Install unit first: bash ml-services/sam-backendctl.sh install" >&2
  exit 1
}

cmd_disable_boot() {
  cmd_stop
  local any=0
  if system_unit_installed; then
    run_systemctl system disable "$UNIT_NAME" || true
    echo "Boot disabled (system): $UNIT_NAME"
    any=1
  fi
  if user_unit_installed; then
    systemctl --user disable "$UNIT_NAME" || true
    echo "Boot disabled (user): $UNIT_NAME"
    any=1
  fi
  if [[ "$any" -eq 0 ]]; then
    echo "No systemd unit installed."
  fi
}

main() {
  local cmd="${1:-status}"
  case "$cmd" in
    -h|--help|help) usage; exit 0 ;;
    status) cmd_status ;;
    start) cmd_start ;;
    stop) cmd_stop ;;
    restart) cmd_restart ;;
    install) cmd_install "${2:-}" ;;
    enable-boot) cmd_enable_boot ;;
    disable-boot) cmd_disable_boot ;;
    *)
      echo "Unknown command: $cmd" >&2
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
