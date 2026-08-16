#!/usr/bin/env bash
# Create offline tarball of data-lab + ego-platform at a git tag (stable backup).
# Usage: ego-workspace-snapshot.sh [tag]   (default: v0.0.12)
set -euo pipefail

TAG="${1:-v0.0.12}"
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORKSPACE="$(dirname "$DATALAB_ROOT")"
PLATFORM="${WORKSPACE}/ego-platform"
BACKUP_DIR="${DATALAB_ROOT}/data-storage/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/ego-workspace-${TAG}-${STAMP}.tar.zst"
MANIFEST="${BACKUP_DIR}/ego-workspace-${TAG}-${STAMP}.manifest.json"

mkdir -p "${BACKUP_DIR}"

cd "${DATALAB_ROOT}"
DL_SHA="$(git rev-parse "${TAG}" 2>/dev/null || git rev-parse HEAD)"
EP_SHA=""
if [[ -d "${PLATFORM}/.git" ]]; then
  cd "${PLATFORM}"
  EP_SHA="$(git rev-parse "${TAG}" 2>/dev/null || git rev-parse HEAD)"
fi

python3 - <<PY
import json
from pathlib import Path
manifest = {
    "tag": ${TAG@Q},
    "created": ${STAMP@Q},
    "repos": {
        "data-lab": {"path": ${str(DATALAB_ROOT)@Q}, "commit": ${DL_SHA@Q}},
        "ego-platform": {"path": ${str(PLATFORM)@Q}, "commit": ${EP_SHA@Q} or None},
    },
    "provision_130": "data-lab-platform/scripts/ego-130-provision.sh server@10.10.10.130 ego-001",
}
Path(${MANIFEST@Q}).write_text(json.dumps(manifest, indent=2) + "\\n")
print(json.dumps(manifest, indent=2))
PY

echo "==> Packing ${OUT}"
tar -C "${WORKSPACE}" \
  --exclude='data-lab/data-storage/stream' \
  --exclude='data-lab/data-storage/corpus' \
  --exclude='data-lab/data-storage/pipeline' \
  --exclude='data-lab/data-storage/logs' \
  --exclude='data-lab/data-storage/samples' \
  --exclude='data-lab/node_modules' \
  --exclude='data-lab/.venv' \
  --exclude='data-lab/**/__pycache__' \
  --exclude='ego-platform/.venv' \
  --exclude='ego-platform/**/__pycache__' \
  -cf - data-lab ego-platform 2>/dev/null | zstd -19 -T0 -o "${OUT}"

echo "==> Done: ${OUT} ($(du -h "${OUT}" | cut -f1))"
echo "    manifest: ${MANIFEST}"
