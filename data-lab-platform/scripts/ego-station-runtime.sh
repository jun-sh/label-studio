#!/usr/bin/env bash
# Resolve per-station runtime defaults (ingest/derive containers, upload URL hints).
set -euo pipefail

ego_station_runtime_env() {
  local station="${1:-}"
  case "$station" in
    ego-001|ego-lab-01|ego-field-02|ego-lan-02|ego-wan-01)
      export STREAM_INGEST_CONTAINER="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
      export DERIVE_WORKER_CONTAINER="${DERIVE_WORKER_CONTAINER:-data-lab-derive-worker-1}"
      ;;
    *)
      : # unknown station — keep caller overrides
      ;;
  esac
  if [[ "$station" == "ego-001" ]]; then
    local _perf="${BASH_SOURCE[0]%/*}/ego-pipeline-performance.env.sh"
    if [[ -f "$_perf" ]]; then
      # shellcheck source=ego-pipeline-performance.env.sh
      source "$_perf"
    fi
  fi
}

ego_process_watch_stations() {
  local raw="${EGO_PROCESS_WATCH_STATIONS:-ego-001}"
  raw="${raw//,/ }"
  echo "$raw"
}
