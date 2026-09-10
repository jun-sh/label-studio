#!/bin/sh
# Ingest 4 official LeRobot v3 sample datasets from repo data-storage/samples/.
set -eu

BUNDLED="${BUNDLED_DATA_ROOT:-/srv/bundled}"
SAMPLES="${DATALAB_SAMPLES_DIR:-/datalab-samples}"
COS_BASE="${LEROBOT_SAMPLES_COS:-https://io-lerobot-examples-1328702871.cos.accelerate.myqcloud.com}"

mkdir -p "${BUNDLED}/covers" "${BUNDLED}/overlays"

copy_if_newer() {
  src="$1"
  dest="$2"
  if [ ! -f "${src}" ]; then
    echo "WARN: missing source ${src}" >&2
    return 1
  fi
  if [ ! -f "${dest}" ] || [ "${src}" -nt "${dest}" ]; then
    echo "==> Copy ${src} -> ${dest}"
    cp -f "${src}" "${dest}"
  else
    echo "==> Up to date ${dest}"
  fi
}

fetch_cover() {
  file="$1"
  dest="${BUNDLED}/covers/${file}"
  if [ -s "${dest}" ]; then
    echo "==> Cover exists ${dest}"
    return 0
  fi
  echo "==> Download cover ${COS_BASE}/${file}"
  wget -q --timeout=120 --tries=3 -O "${dest}" "${COS_BASE}/${file}"
  [ -s "${dest}" ]
}

fetch_archive() {
  file="$1"
  dest="${BUNDLED}/${file}"
  if [ -s "${dest}" ]; then
    echo "==> Archive exists ${dest}"
    return 0
  fi
  echo "==> Download archive ${COS_BASE}/${file}"
  wget -q --timeout=600 --tries=3 -O "${dest}" "${COS_BASE}/${file}"
  [ -s "${dest}" ]
}

valid_zip() {
  [ -s "$1" ] && unzip -t "$1" >/dev/null 2>&1
}

valid_tar() {
  [ -s "$1" ] && tar -tf "$1" >/dev/null 2>&1
}

# --- Local archives (repo data-storage/samples mount) ---
if [ -d "${SAMPLES}" ]; then
  copy_if_newer "${SAMPLES}/SenseXperience Ego.zip" "${BUNDLED}/sensexperience_ego.zip" || true
  copy_if_newer "${SAMPLES}/SenseXperience UMI.zip" "${BUNDLED}/sensexperience_umi.zip" || true
  copy_if_newer "${SAMPLES}/DualAirbot Folding.zip" "${BUNDLED}/dualairbot_fold.zip" || true
  if [ -f "${SAMPLES}/ego_001.zip" ]; then
    copy_if_newer "${SAMPLES}/ego_001.zip" "${BUNDLED}/ego_001.zip" || true
  else
    copy_if_newer "${SAMPLES}/egodome.zip" "${BUNDLED}/ego_001.zip" || true
  fi
  if [ -d "${SAMPLES}/ego_001/dataset" ]; then
    mkdir -p "${BUNDLED}/ego_001"
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete "${SAMPLES}/ego_001/dataset/" "${BUNDLED}/ego_001/" || true
    else
      rm -rf "${BUNDLED}/ego_001"
      cp -a "${SAMPLES}/ego_001/dataset" "${BUNDLED}/ego_001" || true
    fi
  elif [ -d "${SAMPLES}/egodome/dataset" ]; then
    mkdir -p "${BUNDLED}/ego_001"
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete "${SAMPLES}/egodome/dataset/" "${BUNDLED}/ego_001/" || true
    else
      rm -rf "${BUNDLED}/ego_001"
      cp -a "${SAMPLES}/egodome/dataset" "${BUNDLED}/ego_001" || true
    fi
  fi
  if [ -f "${SAMPLES}/ego_001_hand_kp2d.json" ]; then
    copy_if_newer "${SAMPLES}/ego_001_hand_kp2d.json" "${BUNDLED}/overlays/ego_001_hand_kp2d.json" || true
  else
    copy_if_newer "${SAMPLES}/egodome_hand_kp2d.json" "${BUNDLED}/overlays/ego_001_hand_kp2d.json" || true
  fi
  if [ -f "${SAMPLES}/ego_001_depth_preview.json" ]; then
    copy_if_newer "${SAMPLES}/ego_001_depth_preview.json" "${BUNDLED}/overlays/ego_001_depth_preview.json" || true
  else
    copy_if_newer "${SAMPLES}/egodome_depth_preview.json" "${BUNDLED}/overlays/ego_001_depth_preview.json" || true
  fi
  if [ -d "${SAMPLES}/ego_001_depth_preview_frames" ]; then
    mkdir -p "${BUNDLED}/overlays/ego_001_depth_preview_frames"
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete "${SAMPLES}/ego_001_depth_preview_frames/" "${BUNDLED}/overlays/ego_001_depth_preview_frames/" || true
    else
      rm -rf "${BUNDLED}/overlays/ego_001_depth_preview_frames"
      cp -a "${SAMPLES}/ego_001_depth_preview_frames" "${BUNDLED}/overlays/" || true
    fi
  elif [ -d "${SAMPLES}/egodome_depth_preview_frames" ]; then
    mkdir -p "${BUNDLED}/overlays/ego_001_depth_preview_frames"
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete "${SAMPLES}/egodome_depth_preview_frames/" "${BUNDLED}/overlays/ego_001_depth_preview_frames/" || true
    else
      rm -rf "${BUNDLED}/overlays/ego_001_depth_preview_frames"
      cp -a "${SAMPLES}/egodome_depth_preview_frames" "${BUNDLED}/overlays/" || true
    fi
  fi
  if [ -f "${SAMPLES}/DualPiper Pulling.zip" ]; then
    copy_if_newer "${SAMPLES}/DualPiper Pulling.zip" "${BUNDLED}/dualpiper_pulling.zip" || true
  elif [ -f "${SAMPLES}/DualPiper Pulling.tar" ]; then
    copy_if_newer "${SAMPLES}/DualPiper Pulling.tar" "${BUNDLED}/dualpiper_pulling.tar" || true
  fi
else
  echo "WARN: samples directory not mounted: ${SAMPLES}" >&2
fi

# --- DualPiper: fallback to official .tar when local zip/tar absent ---
if [ ! -f "${BUNDLED}/dualpiper_pulling.zip" ] && [ ! -f "${BUNDLED}/dualpiper_pulling.tar" ]; then
  fetch_archive "lerobot_dataset_dualpiper_pulling.tar" && mv -f "${BUNDLED}/lerobot_dataset_dualpiper_pulling.tar" "${BUNDLED}/dualpiper_pulling.tar"
fi
if [ -f "${BUNDLED}/dualpiper_pulling.zip" ] && valid_zip "${BUNDLED}/dualpiper_pulling.zip"; then
  :
elif [ -f "${BUNDLED}/dualpiper_pulling.tar" ] && valid_tar "${BUNDLED}/dualpiper_pulling.tar"; then
  :
else
  echo "WARN: dualpiper_pulling archive missing or invalid" >&2
fi

# --- Cover images (static .webp, same as io-ai.tech) ---
fetch_cover "sensexperience_ego.webp"
fetch_cover "ego_001.webp" || copy_if_newer "${SAMPLES}/ego_001.webp" "${BUNDLED}/covers/ego_001.webp" || copy_if_newer "${SAMPLES}/egodome.webp" "${BUNDLED}/covers/ego_001.webp" || true
fetch_cover "sensexperience_umi.webp"
fetch_cover "lerobot_dataset_dualairbot_fold.webp"
fetch_cover "lerobot_dataset_dualpiper_pulling.webp"

# --- Validate required archives ---
errors=0
for f in sensexperience_ego.zip sensexperience_umi.zip dualairbot_fold.zip; do
  if ! valid_zip "${BUNDLED}/${f}"; then
    echo "ERROR: invalid or missing ${BUNDLED}/${f}" >&2
    errors=$((errors + 1))
  fi
done
if [ -f "${BUNDLED}/dualpiper_pulling.zip" ]; then
  valid_zip "${BUNDLED}/dualpiper_pulling.zip" || errors=$((errors + 1))
elif [ -f "${BUNDLED}/dualpiper_pulling.tar" ]; then
  valid_tar "${BUNDLED}/dualpiper_pulling.tar" || errors=$((errors + 1))
else
  echo "ERROR: dualpiper_pulling archive not found" >&2
  errors=$((errors + 1))
fi

if [ "${errors}" -gt 0 ]; then
  echo "==> Ingest completed with ${errors} error(s). Place zips under data-storage/samples/ (see repo README.md)." >&2
  exit 1
fi

echo "==> All 4 datasets + covers ready under ${BUNDLED}"
