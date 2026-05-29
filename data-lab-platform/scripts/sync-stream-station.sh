#!/usr/bin/env bash
# Rebuild v3 parquet sidecars for a live stream station (run on Data Lab host).
set -euo pipefail
STATION_ID="${1:-ego-lan-214}"
CONTAINER="${LEROBOT_CONTAINER:-data-lab-lerobot-1}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

docker cp "${CONTAINER}:/srv/stream/${STATION_ID}" "${TMP}/station"
python3 "${ROOT}/data-lab-platform/lerobot-studio/scripts/sync-stream-parquet.py" "${TMP}/station"
docker cp "${TMP}/station/meta/episodes" "${CONTAINER}:/srv/stream/${STATION_ID}/meta/"
docker cp "${TMP}/station/meta/tasks.jsonl" "${CONTAINER}:/srv/stream/${STATION_ID}/meta/tasks.jsonl"
docker cp "${TMP}/station/data/chunk-000/file-000.parquet" "${CONTAINER}:/srv/stream/${STATION_ID}/data/chunk-000/file-000.parquet"
echo "Synced parquet for ${STATION_ID}"
