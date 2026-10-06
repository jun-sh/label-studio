#!/usr/bin/env bash
# Load station-profiles/*-mcap-production.env and derive convert/preview defaults.
# SSOT: EGO_CAMERA_OPTICS=fisheye|pinhole (ego-003/004 copy ego-002 profile pattern).
set -euo pipefail

ego_station_profile_path() {
  local station="$1"
  local datalab_root="${2:-}"
  if [[ -z "$datalab_root" ]]; then
    datalab_root="$(cd "${BASH_SOURCE[0]%/*}/../.." && pwd)"
  fi
  echo "${datalab_root}/data-lab-platform/config/station-profiles/${station}-mcap-production.env"
}

ego_source_station_profile() {
  local station="$1"
  local datalab_root="${2:-}"
  local profile
  profile="$(ego_station_profile_path "$station" "$datalab_root")"
  if [[ ! -f "$profile" ]]; then
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$profile"
  set +a
  return 0
}

# L2 daily convert: fisheye → rectify, pinhole → passthrough (no undistort on 34).
ego_l2_oak_mode() {
  local optics="${EGO_CAMERA_OPTICS:-fisheye}"
  if [[ -n "${EGO_OAK_MODE:-}" ]]; then
    echo "$EGO_OAK_MODE"
    return 0
  fi
  case "$optics" in
    pinhole) echo "passthrough" ;;
    fisheye | *) echo "rectify" ;;
  esac
}

ego_apply_station_profile_exports() {
  local station="$1"
  local datalab_root="${2:-}"
  if ! ego_source_station_profile "$station" "$datalab_root"; then
    export EGO_CAMERA_OPTICS="${EGO_CAMERA_OPTICS:-fisheye}"
    return 1
  fi
  export EGO_CAMERA_OPTICS="${EGO_CAMERA_OPTICS:-fisheye}"
  export EGO_STATION_ID="${EGO_STATION_ID:-$station}"
  case "$EGO_CAMERA_OPTICS" in
    pinhole)
      export EGO_PREVIEW_REQUIRE_RECTIFIED="${EGO_PREVIEW_REQUIRE_RECTIFIED:-0}"
      ;;
    fisheye | *)
      export EGO_PREVIEW_REQUIRE_RECTIFIED="${EGO_PREVIEW_REQUIRE_RECTIFIED:-1}"
      ;;
  esac
  return 0
}
