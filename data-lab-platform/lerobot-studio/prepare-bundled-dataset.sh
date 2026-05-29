#!/bin/sh
# Build bundled sample zip (LeRobot v3 mini) for same-origin loading.
set -eu

BUNDLED="${BUNDLED_DATA_ROOT:-/srv/bundled}"
ZIP="${BUNDLED}/sensexperience_ego.zip"
SEED="${BUNDLED}/sensexperience_ego"
HF_BASE="${HF_MIRROR_BASE:-https://hf-mirror.com/datasets/lerobot/pusht/resolve/main}"

mkdir -p "${BUNDLED}"

valid_zip() {
  [ -s "${ZIP}" ] && unzip -t "${ZIP}" >/dev/null 2>&1
}

if [ -f "${BUNDLED}/sensexperience_umi.zip" ] && [ -f "${BUNDLED}/dualairbot_fold.zip" ]; then
  echo "==> Full dataset bundle present; skip mini sample build"
  exit 0
fi

if valid_zip; then
  echo "==> Bundled zip already valid: ${ZIP}"
  exit 0
fi

rm -f "${ZIP}"
rm -rf "${SEED}"
mkdir -p "${SEED}"

if [ -f /app/bundled-seed/sensexperience_ego.zip ] && unzip -t /app/bundled-seed/sensexperience_ego.zip >/dev/null 2>&1; then
  cp -f /app/bundled-seed/sensexperience_ego.zip "${ZIP}"
  echo "==> Using image seed zip: ${ZIP}"
  exit 0
fi

echo "==> Building bundled sample from ${HF_BASE} (hf-mirror)..."

fetch() {
  rel="$1"
  dest="${SEED}/${rel}"
  mkdir -p "$(dirname "${dest}")"
  if ! wget -q --timeout=120 --tries=3 -O "${dest}" "${HF_BASE}/${rel}"; then
    echo "ERROR: failed to download ${rel}" >&2
    return 1
  fi
  [ -s "${dest}" ]
}

fetch "meta/info.json"
fetch "meta/stats.json"
fetch "meta/tasks.parquet"
fetch "meta/episodes/chunk-000/file-000.parquet"
fetch "data/chunk-000/file-000.parquet"
fetch "videos/observation.image/chunk-000/file-000.mp4"

(
  cd "${SEED}"
  zip -qr "${ZIP}" meta data videos
)

if ! valid_zip; then
  echo "ERROR: bundled zip invalid after build" >&2
  exit 1
fi

echo "==> Bundled dataset ready: ${ZIP} ($(wc -c < "${ZIP}") bytes)"
