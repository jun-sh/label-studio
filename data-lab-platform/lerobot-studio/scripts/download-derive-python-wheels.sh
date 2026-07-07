#!/usr/bin/env bash
# One-time (or when bumping versions): cache derive Python wheels for offline Docker build.
#
# Targets bookworm Python 3.11 (cp311). CPU torch only — no CUDA wheels.
#
# Usage:
#   bash data-lab-platform/lerobot-studio/scripts/download-derive-python-wheels.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WHEEL_DIR="${ROOT}/vendor/python-wheels"
STAMP="${WHEEL_DIR}/.wheels-ready"
TUNA="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
CPU_INDEX="https://download.pytorch.org/whl/cpu"
TORCH_CPU="torch==2.7.1+cpu"
CP311=(--python-version 311 --platform manylinux_2_28_x86_64 --implementation cp --abi cp311)

mkdir -p "${WHEEL_DIR}"
rm -f "${STAMP}"
rm -f "${WHEEL_DIR}"/*.whl

echo "==> wheel cache: ${WHEEL_DIR} (cp311 / bookworm)"

echo "==> [1/4] CPU torch + torchvision"
pip3 download "${TORCH_CPU}" torchvision \
  --dest "${WHEEL_DIR}" \
  --index-url "${CPU_INDEX}" \
  --extra-index-url "${TUNA}" \
  "${CP311[@]}" --only-binary=:all: \
  || pip3 download "${TORCH_CPU}" torchvision --dest "${WHEEL_DIR}" \
       --index-url "${CPU_INDEX}" --extra-index-url "${TUNA}"

echo "==> [2/4] pure-python packages"
pip3 download --dest "${WHEEL_DIR}" -i "${TUNA}" \
  "datasets>=4.0.0,<5.0.0" "huggingface_hub>=0.34.0" \
  einops "jsonlines<5.0.0,>=4.0.0" "packaging<26.0,>=24.2"

echo "==> [2b/4] lerobot import-chain runtime (--no-deps each)"
for pkg in \
  "accelerate<2.0.0,>=1.10.0" safetensors psutil draccus==0.10.0 \
  "deepdiff<9.0.0,>=7.0.1" "termcolor<4.0.0,>=2.4.0" "imageio>=2.34.0" \
  "setuptools>=71.0.0" orderly mergedeep mypy-extensions typing-inspect \
  pyyaml-include toml; do
  pip3 download --dest "${WHEEL_DIR}" -i "${TUNA}" --no-deps "${pkg}" || true
done
# Drop CUDA / duplicate pure-python wheels that break offline bulk install.
rm -f "${WHEEL_DIR}"/nvidia_*.whl "${WHEEL_DIR}"/cuda*.whl 2>/dev/null || true
for pkg in networkx fsspec filelock; do
  ls -1 "${WHEEL_DIR}/${pkg}-"*.whl 2>/dev/null | sort -V | head -n -1 | xargs -r rm -f
done

echo "==> [3/4] cp311 binary wheels"
pip3 download --dest "${WHEEL_DIR}" -i "${TUNA}" \
  "${CP311[@]}" --only-binary=:all: \
  "numpy>=1.26.0" "Pillow>=10.0.0" \
  "opencv-python-headless>=4.9.0" "av<16.0.0,>=15.0.0" \
  "pyarrow>=21.0.0" pandas xxhash aiohttp pyyaml charset-normalizer
# Remove cp310 wheels only where a cp311 build exists (avoid ABI mismatch).
for base in numpy pyarrow pandas; do
  if compgen -G "${WHEEL_DIR}/${base}-*cp311*.whl" >/dev/null; then
    rm -f "${WHEEL_DIR}/${base}-"*cp310-cp310*.whl
  fi
done
rm -f "${WHEEL_DIR}"/*.tar.gz
# datasets pins fsspec<=2026.2.0; torch may pull a newer fsspec — keep the older pin.
if [[ -f "${WHEEL_DIR}/fsspec-2026.2.0-py3-none-any.whl" ]]; then
  rm -f "${WHEEL_DIR}"/fsspec-2026.6.0-*.whl
fi

echo "==> [4/4] lerobot (no-deps — keeps CPU torch)"
pip3 download "lerobot==0.4.4" --no-deps \
  --dest "${WHEEL_DIR}" \
  -i "${TUNA}"

count="$(find "${WHEEL_DIR}" -maxdepth 1 -name '*.whl' | wc -l | tr -d ' ')"
du -sh "${WHEEL_DIR}"
if [[ "${count}" -lt 15 ]]; then
  echo "ERROR: expected many .whl files, got ${count}" >&2
  exit 1
fi
if ! ls "${WHEEL_DIR}"/torch-*+cpu*cp311*.whl >/dev/null 2>&1; then
  echo "ERROR: missing cp311 CPU torch wheel" >&2
  exit 1
fi
if ! ls "${WHEEL_DIR}"/numpy-*cp311*.whl >/dev/null 2>&1; then
  echo "ERROR: missing cp311 numpy wheel" >&2
  exit 1
fi

date -Is > "${STAMP}"
echo "OK: ${count} wheels cached. Rebuild:"
echo "  docker-compose -f docker-compose.yml -f data-lab-platform/docker-compose.platform.yml build stream-ingest"
