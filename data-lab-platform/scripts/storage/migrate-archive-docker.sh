#!/usr/bin/env bash
# Session-level cold migration inside stream-ingest (archive/ → /cold-archive/*.tar.gz).
set -euo pipefail

ENV_FILE="${STREAM_STORAGE_ENV:-/opt/datalab/env/stream-storage.env}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

STATION_ID="${STATION_ID:-ego-001}"
CONTAINER="${STREAM_INGEST_CONTAINER:-data-lab-stream-ingest-1}"
COLD_IN_CONTAINER="${COLD_IN_CONTAINER:-/cold-archive}"
MODE="${1:-enforce}"
QUOTA_GB="${STREAM_QUOTA_GB:-20}"
HIGH_PCT="${HOT_HIGH_WATER_PERCENT:-90}"
MAX_MOVE="${COLD_MIGRATE_MAX_PER_RUN:-3}"
MIN_AGE_HOURS="${HOT_ARCHIVE_MIN_AGE_HOURS:-168}"
LOG="${MIGRATE_DOCKER_LOG:-/opt/datalab/log/migrate-archive-docker.log}"

mkdir -p "$(dirname "$LOG")" "${COLD_ROOT:-/media/user01/7234c6f9-112e-4b82-925d-7b86065a5f4a/workspace/data-lab/data-storage/ego-archive}"
exec >>"$LOG" 2>&1

echo "=== $(date -Is) migrate-archive-docker mode=${MODE} station=${STATION_ID} ==="

docker exec \
  -e STATION_ID="$STATION_ID" \
  -e MODE="$MODE" \
  -e COLD_IN_CONTAINER="$COLD_IN_CONTAINER" \
  -e QUOTA_GB="$QUOTA_GB" \
  -e HIGH_PCT="$HIGH_PCT" \
  -e MAX_MOVE="$MAX_MOVE" \
  -e MIN_AGE_HOURS="$MIN_AGE_HOURS" \
  "$CONTAINER" \
  node -e '
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const stationId = process.env.STATION_ID;
const root = "/srv/stream/" + stationId;
const archive = path.join(root, "archive");
const coldRoot = process.env.COLD_IN_CONTAINER || "/cold-archive";
const mode = process.env.MODE || "enforce";
const quota = Number(process.env.QUOTA_GB) * 1024 ** 3;
const high = Math.floor((quota * Number(process.env.HIGH_PCT)) / 100);
const maxMove = Number(process.env.MAX_MOVE);
const minAgeMs = Number(process.env.MIN_AGE_HOURS) * 3600 * 1000;

function usageBytes() {
  const hk = path.join(root, "live", "disk-housekeeping.json");
  if (!fs.existsSync(hk)) return 0;
  try {
    return Number(JSON.parse(fs.readFileSync(hk, "utf8")).usageBytes) || 0;
  } catch {
    return 0;
  }
}

function listArchives() {
  if (!fs.existsSync(archive)) return [];
  return fs
    .readdirSync(archive, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => {
      const p = path.join(archive, d.name);
      return { name: d.name, path: p, mtime: fs.statSync(p).mtimeMs };
    })
    .sort((a, b) => a.mtime - b.mtime);
}

fs.mkdirSync(coldRoot, { recursive: true });
let usage = usageBytes();
let moved = 0;
for (const ent of listArchives()) {
  if (moved >= maxMove) break;
  const ageOk = Date.now() - ent.mtime >= minAgeMs;
  if (mode === "age" && !ageOk) continue;
  if (mode === "enforce" && usage <= high) break;
  const out = path.join(coldRoot, ent.name + ".tar.gz");
  if (fs.existsSync(out)) {
    fs.rmSync(ent.path, { recursive: true, force: true });
    moved += 1;
    usage = usageBytes();
    continue;
  }
  execSync(
    "tar -czf " +
      JSON.stringify(out) +
      " -C " +
      JSON.stringify(archive) +
      " " +
      JSON.stringify(ent.name),
  );
  fs.rmSync(ent.path, { recursive: true, force: true });
  moved += 1;
  usage = usageBytes();
}
console.log(JSON.stringify({ ok: true, mode, usageBytes: usage, highWaterBytes: high, moved }));
'

echo "=== done ==="
