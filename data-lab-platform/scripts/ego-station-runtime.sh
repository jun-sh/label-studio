#!/usr/bin/env bash
# Resolve per-station runtime defaults (ingest/derive containers, upload URL hints).
# Source from ego-process / ego-derive / ego-process-watcher.
set -euo pipefail

# Usage: ego_station_runtime_env <station_id>
ego_station_runtime_env() {
  local station="${1:-}"
  case "$station" in
    ego-mcap-pilot)
      export STREAM_INGEST_CONTAINER="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-mcap-pilot-1}"
      export DERIVE_WORKER_CONTAINER="${DERIVE_WORKER_CONTAINER:-data-lab-derive-worker-mcap-pilot-1}"
      ;;
    ego-001|ego-lab-01|ego-field-02|ego-lan-02|ego-wan-01)
      : # production defaults (stream-ingest-1 / derive-worker-1)
      ;;
    *)
      : # unknown station — keep caller overrides
      ;;
  esac
}

# Comma- or space-separated station list for ego-process-watcher.
ego_process_watch_stations() {
  local raw="${EGO_PROCESS_WATCH_STATIONS:-ego-001,ego-mcap-pilot}"
  raw="${raw//,/ }"
  echo "$raw"
}
