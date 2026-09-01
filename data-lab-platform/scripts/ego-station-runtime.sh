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
}

ego_process_watch_stations() {
  local raw="${EGO_PROCESS_WATCH_STATIONS:-ego-001}"
  raw="${raw//,/ }"
  echo "$raw"
}
