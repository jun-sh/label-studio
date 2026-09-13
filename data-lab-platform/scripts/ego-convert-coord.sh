#!/usr/bin/env bash
# Mutual exclusion between inline L2 convert (ego-run-l2 / ego-process) and
# background ego-convert-worker. Prevents concurrent ffmpeg rectify on the same session.
set -euo pipefail

_ego_convert_coord_root() {
  local datalab_root="${1:-}"
  if [[ -z "$datalab_root" ]]; then
    datalab_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  fi
  printf '%s' "$datalab_root"
}

_ego_convert_coord_lockfile() {
  local station="$1"
  local datalab_root
  datalab_root="$(_ego_convert_coord_root "${2:-}")"
  echo "${datalab_root}/data-storage/pipeline/.inline-convert-${station}.lock"
}

_ego_convert_coord_pidfile() {
  local station="$1"
  local datalab_root
  datalab_root="$(_ego_convert_coord_root "${2:-}")"
  echo "${datalab_root}/data-storage/pipeline/.convert-worker-${station}.pid"
}

ego_convert_coord_stop_worker() {
  local station="$1"
  local datalab_root="${2:-}"
  datalab_root="$(_ego_convert_coord_root "$datalab_root")"
  local pf pid opid
  pf="$(_ego_convert_coord_pidfile "$station" "$datalab_root")"
  pid=""
  if [[ -f "$pf" ]]; then
    pid="$(tr -d '[:space:]' < "$pf" 2>/dev/null || true)"
  fi
  while read -r opid; do
    [[ -z "$opid" ]] && continue
    if kill -0 "$opid" 2>/dev/null; then
      echo "[convert-coord] stopping convert worker pid=${opid} station=${station}"
      kill -TERM "$opid" 2>/dev/null || true
    fi
  done < <(pgrep -f "ego-convert-worker\\.sh[[:space:]]+${station}(\\s|$)" 2>/dev/null || true)
  if [[ -n "$pid" ]]; then
    sleep 1
    kill -KILL "$pid" 2>/dev/null || true
  fi
  while read -r opid; do
    [[ -z "$opid" ]] && continue
    kill -KILL "$opid" 2>/dev/null || true
  done < <(pgrep -f "ego-convert-worker\\.sh[[:space:]]+${station}(\\s|$)" 2>/dev/null || true)
  rm -f "$pf"
}

ego_convert_coord_stop_orphan_pipelines() {
  local station="$1"
  local keep_pid="${2:-$$}"
  local opid
  while read -r opid; do
    [[ -z "$opid" || "$opid" == "$keep_pid" ]] && continue
    if kill -0 "$opid" 2>/dev/null; then
      echo "[convert-coord] stopping orphan ego-run-pipeline pid=${opid} station=${station}"
      kill -TERM "$opid" 2>/dev/null || true
    fi
  done < <(pgrep -f "ego-run-pipeline[[:space:]]+${station}(\\s|$)" 2>/dev/null || true)
  sleep 1
  while read -r opid; do
    [[ -z "$opid" || "$opid" == "$keep_pid" ]] && continue
    kill -KILL "$opid" 2>/dev/null || true
  done < <(pgrep -f "ego-run-pipeline[[:space:]]+${station}(\\s|$)" 2>/dev/null || true)
}

ego_convert_coord_acquire_inline() {
  local station="$1"
  local datalab_root="${2:-}"
  datalab_root="$(_ego_convert_coord_root "$datalab_root")"
  local lock
  lock="$(_ego_convert_coord_lockfile "$station" "$datalab_root")"
  mkdir -p "$(dirname "$lock")"
  ego_convert_coord_stop_worker "$station" "$datalab_root"
  ego_convert_coord_stop_orphan_pipelines "$station" "$$"
  if [[ -f "$lock" ]]; then
    local old_pid
    old_pid="$(tr -d '[:space:]' < "$lock" 2>/dev/null || true)"
    if [[ -n "$old_pid" && "$old_pid" != "$$" ]] && kill -0 "$old_pid" 2>/dev/null; then
      echo "[convert-coord] waiting for inline convert pid=${old_pid} station=${station}"
      local deadline=$((SECONDS + 30))
      while (( SECONDS < deadline )); do
        kill -0 "$old_pid" 2>/dev/null || break
        sleep 1
      done
      if kill -0 "$old_pid" 2>/dev/null; then
        echo "[convert-coord] stale inline convert pid=${old_pid} — terminating"
        kill -TERM "$old_pid" 2>/dev/null || true
        sleep 1
        kill -KILL "$old_pid" 2>/dev/null || true
      fi
    fi
  fi
  echo "$$" > "$lock"
}

ego_convert_coord_release_inline() {
  local station="$1"
  local datalab_root="${2:-}"
  datalab_root="$(_ego_convert_coord_root "$datalab_root")"
  local lock
  lock="$(_ego_convert_coord_lockfile "$station" "$datalab_root")"
  if [[ -f "$lock" ]]; then
    local owner
    owner="$(tr -d '[:space:]' < "$lock" 2>/dev/null || true)"
    if [[ -z "$owner" || "$owner" == "$$" ]]; then
      rm -f "$lock"
    fi
  fi
}

ego_convert_coord_inline_active() {
  local station="$1"
  local datalab_root="${2:-}"
  datalab_root="$(_ego_convert_coord_root "$datalab_root")"
  local lock pid
  lock="$(_ego_convert_coord_lockfile "$station" "$datalab_root")"
  [[ -f "$lock" ]] || return 1
  pid="$(tr -d '[:space:]' < "$lock" 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}
