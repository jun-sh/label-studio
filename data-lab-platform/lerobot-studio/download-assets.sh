#!/bin/sh
# Pin visualizer static release (fixed manifest + chunks referenced by index bundle).
set -eu

BASE_URL="${LEROBOT_STUDIO_UPSTREAM:-https://io-ai.tech}"
OUT="${LEROBOT_STUDIO_ROOT:-/srv/lerobot}"

mkdir -p "${OUT}/assets"

fetch() {
  rel="$1"
  sub="${rel#/lerobot}"
  [ -n "$sub" ] || sub="/index.html"
  dest="${OUT}${sub}"
  mkdir -p "$(dirname "$dest")"
  echo "GET ${BASE_URL}${rel}"
  wget -q --timeout=60 --tries=3 -O "$dest" "${BASE_URL}${rel}"
}

fetch /lerobot/index.html
fetch /lerobot/logo.svg

for f in \
  assets/index-BM8rEaYC.js \
  assets/index-BEypbk1x.css \
  assets/vendor-BQJQ5ijy.js \
  assets/rolldown-runtime-BYbx6iT9.js \
  assets/apache-arrow-DdfZMtFd.js \
  assets/dockview-jD63u09l.js \
  assets/dockview-CrgMxqRm.css \
  assets/zip-B2J92Cm-.js \
  assets/parquet-wasm-ByslZQWX.js \
  assets/parquet_wasm_bg-DcKVfvto.wasm \
  assets/LeRobotDataLoader-TxQpt3xk.js \
  assets/DockviewLayout-B30e0sME.js \
  assets/DockviewLayout-BWx-G9oJ.css \
  assets/MediaCache-CwZqT4Ur.js \
  assets/uplot-B8nCPBqd.js \
  assets/uplot-BHzERDP4.css
do
  fetch "/lerobot/${f}"
done

test -f "${OUT}/index.html"
test -f "${OUT}/assets/index-BM8rEaYC.js"
test -f "${OUT}/assets/LeRobotDataLoader-TxQpt3xk.js"
echo "Visualizer static assets ready under ${OUT}"
