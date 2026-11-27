#!/usr/bin/env bash
# Safe storage cleanup for ego stream stations and LeRobot QC video cache.
#
# NEVER deletes (QC annotation truth — http://10.10.10.34:8080/qc):
#   data-storage/lerobot-qc/sidecar/
#   data-storage/lerobot-qc/delivery/
#   data-storage/lerobot-qc/collections.registry.json
#
# Stream mode removes only:
#   - .upload/incoming/*.mcap.zst when raw/segments already has that segment id
#   - derived/_tmp.* scratch dirs
#
# QC trim-cache mode removes only:
#   - data-storage/lerobot-qc/cache/trimmed_videos/*
#
# Usage:
#   bash ego-safe-storage-cleanup.sh                    # stream dry-run
#   bash ego-safe-storage-cleanup.sh --apply            # stream apply
#   bash ego-safe-storage-cleanup.sh --apply --station ego-002
#   bash ego-safe-storage-cleanup.sh --qc-trim-cache    # QC cache dry-run
#   bash ego-safe-storage-cleanup.sh --qc-trim-cache --apply
#   bash ego-safe-storage-cleanup.sh --backup-qc-sidecar
#   bash ego-safe-storage-cleanup.sh --backup-qc-sidecar --apply
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STREAM_ROOT="${EGO_STREAM_ROOT:-${ROOT}/data-storage/stream}"
QC_ROOT="${LEROBOT_QC_ROOT:-${ROOT}/data-storage/lerobot-qc}"
QC_SIDECAR="${QC_ROOT}/sidecar"
QC_DELIVERY="${QC_ROOT}/delivery"
QC_REGISTRY="${QC_ROOT}/collections.registry.json"
QC_TRIM_CACHE="${QC_ROOT}/cache/trimmed_videos"
QC_BACKUP_DIR="${QC_ROOT}/backups"
QC_DOCKER_CONTAINER="${LEROBOT_QC_DOCKER:-datalab-qc-1}"

STATION=""
APPLY=0
MODE="stream" # stream | qc-trim-cache | backup-qc-sidecar

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --station) STATION="$2"; shift 2 ;;
    --station=*) STATION="${1#*=}"; shift ;;
    --qc-trim-cache) MODE="qc-trim-cache"; shift ;;
    --backup-qc-sidecar) MODE="backup-qc-sidecar"; shift ;;
    -h|--help)
      sed -n '2,26p' "$0"
      exit 0
      ;;
    *)
      if [[ -z "${STATION}" ]]; then
        STATION="$1"
      else
        echo "Unknown arg: $1" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

log() { echo "[ego-safe-cleanup] $*"; }

abort_if_qc_protected_target() {
  local target="$1"
  local resolved
  resolved="$(readlink -f "${target}" 2>/dev/null || realpath "${target}" 2>/dev/null || echo "${target}")"
  local sidecar delivery registry
  sidecar="$(readlink -f "${QC_SIDECAR}" 2>/dev/null || realpath "${QC_SIDECAR}" 2>/dev/null || echo "${QC_SIDECAR}")"
  delivery="$(readlink -f "${QC_DELIVERY}" 2>/dev/null || realpath "${QC_DELIVERY}" 2>/dev/null || echo "${QC_DELIVERY}")"
  registry="$(readlink -f "${QC_REGISTRY}" 2>/dev/null || realpath "${QC_REGISTRY}" 2>/dev/null || echo "${QC_REGISTRY}")"
  case "${resolved}" in
    "${sidecar}"|"${sidecar}"/*)
      log "REFUSE: path is under QC sidecar (annotations): ${target}"
      exit 1
      ;;
    "${delivery}"|"${delivery}"/*)
      log "REFUSE: path is under QC delivery: ${target}"
      exit 1
      ;;
    "${registry}")
      log "REFUSE: path is QC collections.registry.json"
      exit 1
      ;;
  esac
  if [[ "${resolved}" == "$(readlink -f "${QC_ROOT}" 2>/dev/null || realpath "${QC_ROOT}" 2>/dev/null)" ]]; then
    log "REFUSE: never rm -rf entire lerobot-qc root; use --qc-trim-cache only for cache"
    exit 1
  fi
}

run_stream_cleanup() {
  local stations=()
  if [[ -n "${STATION}" ]]; then
    stations+=("${STATION}")
  else
    for d in "${STREAM_ROOT}"/ego-*; do
      [[ -d "${d}" ]] && stations+=("$(basename "${d}")")
    done
  fi

  local removed_incoming=0
  local removed_tmp=0

  for st in "${stations[@]}"; do
    local base="${STREAM_ROOT}/${st}"
    [[ -d "${base}" ]] || continue
    local incoming="${base}/.upload/incoming"
    local raw="${base}/raw/segments"
    if [[ -d "${incoming}" && -d "${raw}" ]]; then
      for f in "${incoming}"/seg_*.mcap.zst; do
        [[ -f "${f}" ]] || continue
        local fname base_noext seg
        fname="$(basename "${f}")"
        base_noext="${fname%.mcap.zst}"
        seg="$(sed -E 's/_[0-9]+$//' <<<"${base_noext}")"
        if compgen -G "${raw}/*/${seg}.mcap*" > /dev/null; then
          if [[ "${APPLY}" -eq 1 ]]; then
            rm -f "${f}"
            log "removed incoming duplicate: ${st}/${seg}"
          else
            log "would remove incoming duplicate: ${st}/${seg} (${f})"
          fi
          removed_incoming=$((removed_incoming + 1))
        fi
      done
    fi
    while IFS= read -r -d '' td; do
      abort_if_qc_protected_target "${td}"
      if [[ "${APPLY}" -eq 1 ]]; then
        rm -rf "${td}"
        log "removed derive scratch: ${td}"
      else
        log "would remove derive scratch: ${td}"
      fi
      removed_tmp=$((removed_tmp + 1))
    done < <(find "${base}" -type d -path '*/derived/_tmp.*' -print0 2>/dev/null)
  done

  if [[ "${APPLY}" -eq 0 ]]; then
    log "stream dry-run: incoming=${removed_incoming} tmp_dirs=${removed_tmp}; use --apply"
  else
    log "stream done: incoming=${removed_incoming} tmp_dirs=${removed_tmp}"
  fi
}

run_qc_trim_cache_cleanup() {
  abort_if_qc_protected_target "${QC_TRIM_CACHE}"
  if [[ ! -d "${QC_TRIM_CACHE}" ]]; then
    log "QC trim cache missing (nothing to do): ${QC_TRIM_CACHE}"
    return 0
  fi
  local bytes=0
  if command -v du >/dev/null 2>&1; then
    bytes="$(du -sb "${QC_TRIM_CACHE}" 2>/dev/null | awk '{print $1}')"
  fi
  local count=0
  shopt -s nullglob
  local entries=("${QC_TRIM_CACHE}"/*)
  shopt -u nullglob
  count="${#entries[@]}"
  if [[ "${count}" -eq 0 ]]; then
    log "QC trim cache already empty: ${QC_TRIM_CACHE}"
    return 0
  fi
  if [[ "${APPLY}" -eq 1 ]]; then
    # Too many files for rm * (ARG_MAX). Cache files are often root-owned (lerobot-qc container).
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "${QC_DOCKER_CONTAINER}"; then
      docker exec "${QC_DOCKER_CONTAINER}" find /data/cache/trimmed_videos -mindepth 1 -delete
      log "removed QC trim cache via docker ${QC_DOCKER_CONTAINER} (~${bytes} bytes)"
    else
      find "${QC_TRIM_CACHE}" -mindepth 1 -delete 2>/dev/null || {
        log "host delete failed (try root or set LEROBOT_QC_DOCKER=${QC_DOCKER_CONTAINER})"
        exit 1
      }
      log "removed QC trim cache on host (~${bytes} bytes) under ${QC_TRIM_CACHE}"
    fi
  else
    log "would remove QC trim cache: ${count} entries (~${bytes} bytes) under ${QC_TRIM_CACHE}"
    log "QC protected (untouched): sidecar, delivery, collections.registry.json"
  fi
  if [[ "${APPLY}" -eq 0 ]]; then
    log "qc-trim-cache dry-run; use --qc-trim-cache --apply"
  fi
}

run_backup_qc_sidecar() {
  if [[ ! -d "${QC_SIDECAR}" ]]; then
    log "QC sidecar missing: ${QC_SIDECAR}"
    exit 1
  fi
  mkdir -p "${QC_BACKUP_DIR}"
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  local archive="${QC_BACKUP_DIR}/qc-sidecar-${stamp}.tar.gz"
  if [[ "${APPLY}" -eq 1 ]]; then
    tar -czf "${archive}" -C "${QC_ROOT}" sidecar
    log "QC sidecar backup: ${archive} ($(du -h "${archive}" | awk '{print $1}'))"
  else
    log "would backup ${QC_SIDECAR} -> ${archive}"
    log "backup dry-run; use --backup-qc-sidecar --apply"
  fi
}

case "${MODE}" in
  stream) run_stream_cleanup ;;
  qc-trim-cache) run_qc_trim_cache_cleanup ;;
  backup-qc-sidecar) run_backup_qc_sidecar ;;
  *)
    echo "Unknown mode: ${MODE}" >&2
    exit 2
    ;;
esac
