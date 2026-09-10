#!/usr/bin/env bash
# Start ROS station stack: roscore → multi_cam_publisher → rosbridge → sribridge relay.
set -eo pipefail

log() { echo "[ros-station] $(date '+%F %T') $*"; }

# --- ROS environment ---
export ROS_DISTRO="${ROS_DISTRO:-noetic}"
if [[ -f /opt/ros/noetic/setup.bash ]]; then
  # shellcheck disable=SC1091
  source /opt/ros/noetic/setup.bash
elif [[ -f /opt/ros/melodic/setup.bash ]]; then
  # shellcheck disable=SC1091
  source /opt/ros/melodic/setup.bash
else
  log "ERROR: ROS not found under /opt/ros/{noetic,melodic}"
  exit 1
fi

for ws in \
  "${ROS_STATION_WS:-}" \
  "${HOME}/catkin_ws/devel/setup.bash" \
  "/home/server/catkin_ws/devel/setup.bash" \
  "/home/server/workspace/catkin_ws/devel/setup.bash"; do
  if [[ -n "${ws}" && -f "${ws}" ]]; then
    # shellcheck disable=SC1090
    source "${ws}"
    log "sourced workspace: ${ws}"
    break
  fi
done

PIDS=()
cleanup() {
  log "stopping children..."
  for pid in "${PIDS[@]}"; do
    kill "${pid}" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM

wait_for_roscore() {
  local i
  for i in $(seq 1 60); do
    if rosparam list &>/dev/null; then
      return 0
    fi
    sleep 1
  done
  log "ERROR: roscore did not become ready within 60s"
  return 1
}

wait_for_cameras() {
  local cam dev
  local cams=(
    "/dev/v4l/by-path/pci-0000:00:14.0-usb-0:1:1.0-video-index0"
    "/dev/v4l/by-path/pci-0000:00:14.0-usb-0:2:1.0-video-index0"
    "/dev/v4l/by-path/pci-0000:00:14.0-usb-0:9:1.0-video-index0"
  )
  local i
  for i in $(seq 1 120); do
    local ready=1
    for cam in "${cams[@]}"; do
      if [[ ! -e "${cam}" ]]; then
        ready=0
        break
      fi
    done
    if [[ "${ready}" -eq 1 ]]; then
      log "all USB cameras ready"
      return 0
    fi
    sleep 1
  done
  log "WARN: cameras not ready after 120s; will retry in multi_cam loop"
  return 1
}

run_multi_cam_loop() {
  while rosparam list &>/dev/null; do
    wait_for_cameras || true
    if wait_for_display; then
      rosparam set use_gui true
    else
      rosparam set use_gui false
    fi
    log "starting multi_cam_publisher (use_gui=$(rosparam get use_gui 2>/dev/null || echo unknown))"
    rosrun multi_cam_publisher multi_cam_publisher.py || true
    log "multi_cam_publisher exited; retry in 5s"
    sleep 5
  done
}

wait_for_display() {
  local i d
  for i in $(seq 1 180); do
    for d in 0 1; do
      if [[ -S "/tmp/.X11-unix/X${d}" && -f "${HOME}/.Xauthority" ]]; then
        export DISPLAY=":${d}"
        export XAUTHORITY="${HOME}/.Xauthority"
        log "display ready: DISPLAY=${DISPLAY}"
        return 0
      fi
    done
    sleep 2
  done
  log "WARN: no X display after 6min; running multi_cam headless"
  unset DISPLAY
  return 1
}

log "starting roscore"
roscore &
PIDS+=($!)
wait_for_roscore

log "starting rosbridge_websocket"
roslaunch rosbridge_server rosbridge_websocket.launch &
PIDS+=($!)

log "starting sribridge relay_bridge"
roslaunch sribridge relay_bridge.launch &
PIDS+=($!)

run_multi_cam_loop &
PIDS+=($!)

log "all processes started (pids: ${PIDS[*]})"
# Keep service alive while roscore runs; systemd restarts if master dies.
wait "${PIDS[0]}"
log "roscore exited; systemd will restart the service"
exit 1
