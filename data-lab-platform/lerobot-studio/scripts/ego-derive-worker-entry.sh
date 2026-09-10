#!/bin/sh
# Q1-4: derive-worker entry — unique DERIVE_WORKER_ID + worker count for CPU budget split.
set -eu

if [ -z "${DERIVE_WORKER_ID:-}" ]; then
  name="${HOSTNAME:-derive-worker-1}"
  case "$name" in
    derive-worker-[0-9]*)
      DERIVE_WORKER_ID="$name"
      ;;
    *derive-worker-*)
      suffix="${name##*derive-worker-}"
      DERIVE_WORKER_ID="derive-worker-${suffix}"
      ;;
    [0-9]*)
      DERIVE_WORKER_ID="derive-worker-${name}"
      ;;
    *-*)
      suffix="${name##*-}"
      DERIVE_WORKER_ID="derive-worker-${suffix}"
      ;;
    *)
      DERIVE_WORKER_ID="derive-worker-${name}"
      ;;
  esac
  export DERIVE_WORKER_ID
fi

export DERIVE_WORKER_COUNT="${DERIVE_WORKER_COUNT:-1}"

echo "[ego-derive-worker-entry] workerId=${DERIVE_WORKER_ID} workerCount=${DERIVE_WORKER_COUNT} hostname=${HOSTNAME:-unknown}"

exec node /app/ego-derive-watch.mjs
