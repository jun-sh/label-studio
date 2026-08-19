#!/usr/bin/env bash
# Wipe 34 stream derived artifacts; ALWAYS preserve raw/segments/*.tar.zst (Phase0 §9.3).
set -euo pipefail

ego_reset_wipe_stream_derived() {
  local stream_host="$1"
  local station_id="${2:-}"

  if [[ -z "${stream_host}" || ! -d "${stream_host}" ]]; then
    mkdir -p "${stream_host}/raw/segments"
    return 0
  fi

  local raw_backup=""
  if [[ -d "${stream_host}/raw/segments" ]]; then
    raw_backup="$(mktemp -d "${TMPDIR:-/tmp}/ego-raw-backup.XXXXXX")"
    mkdir -p "${raw_backup}"
    cp -a "${stream_host}/raw/segments/." "${raw_backup}/"
  fi

  for rel in \
    data videos sensor_raw staging _staging live \
    state/sessions meta/episodes \
    .upload .mux pipeline; do
    rm -rf "${stream_host}/${rel}" 2>/dev/null || true
  done

  rm -f "${stream_host}/meta/info.json" 2>/dev/null || true
  rm -f "${stream_host}/state/deriver.lock" 2>/dev/null || true
  rm -rf "${stream_host}/state/segments" 2>/dev/null || true

  mkdir -p "${stream_host}/raw/segments"
  if [[ -n "${raw_backup}" && -d "${raw_backup}" ]]; then
    cp -a "${raw_backup}/." "${stream_host}/raw/segments/"
    rm -rf "${raw_backup}"
  fi

  if [[ -n "${station_id}" ]]; then
    echo "[wipe-stream] ${station_id}: derived cleared; raw/segments preserved"
  fi
}

ego_reset_wipe_stream_derived_docker() {
  local stream_mount_root="$1"
  local station_id="$2"
  docker run --rm -v "${stream_mount_root}:/srv/stream" alpine sh -eu -c "
    station=/srv/stream/${station_id}
    backup=\$(mktemp -d)
    if [ -d \"\${station}/raw/segments\" ]; then
      mkdir -p \"\${backup}/segments\"
      cp -a \"\${station}/raw/segments/.\" \"\${backup}/segments/\"
    fi
    for rel in data videos sensor_raw staging _staging live state/sessions meta/episodes .upload .mux pipeline; do
      rm -rf \"\${station}/\${rel}\" 2>/dev/null || true
    done
    rm -f \"\${station}/meta/info.json\" 2>/dev/null || true
    rm -f \"\${station}/state/deriver.lock\" 2>/dev/null || true
    rm -rf \"\${station}/state/segments\" 2>/dev/null || true
    mkdir -p \"\${station}/raw/segments\"
    if [ -d \"\${backup}/segments\" ]; then
      cp -a \"\${backup}/segments/.\" \"\${station}/raw/segments/\"
    fi
    rm -rf \"\${backup}\"
    chown -R 1000:1000 \"\${station}\" 2>/dev/null || true
  "
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  STREAM_HOST="${1:?stream host path required}"
  STATION_ID="${2:-}"
  ego_reset_wipe_stream_derived "${STREAM_HOST}" "${STATION_ID}"
fi
