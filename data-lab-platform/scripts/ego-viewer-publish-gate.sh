#!/usr/bin/env bash
# Post-deploy viewer publish gate (B7): 4 cameras, depth PNGs, zip integrity.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SESSIONS_PY="${SCRIPT_DIR}/ego-pipeline-sessions.py"
SLUG="${1:-}"
STATION="${EGO_STATION:-ego-001}"

if [[ -z "$SLUG" ]]; then
  SLUG="$(python3 "$SESSIONS_PY" slug "$STATION" --datalab-root "$DATALAB_ROOT" 2>/dev/null || echo "ego_001")"
fi

[[ -n "$SLUG" ]] || {
  echo "用法: ego-viewer-publish-gate.sh <corpus_slug>" >&2
  exit 2
}

CORPUS="${DATALAB_ROOT}/data-storage/corpus/${SLUG}"
HTTP_DATASET="${DATALAB_ROOT}/data-storage/samples/${SLUG}/dataset"
SAMPLES_ZIP="${DATALAB_ROOT}/data-storage/samples/${SLUG}.zip"
DEPTH_JSON="${DATALAB_ROOT}/data-storage/samples/${SLUG}_depth_preview.json"
DEPTH_FRAMES="${DATALAB_ROOT}/data-storage/samples/${SLUG}_depth_preview_frames"

export PYTHONPATH="${DATALAB_ROOT}/../ego-platform/src:${PYTHONPATH:-}"

python3 << PY
import json
import os
import sys
from pathlib import Path

from ego_platform.lerobot.publish_gate import validate_viewer_publish

corpus = Path("${CORPUS}")
http_dir = Path("${HTTP_DATASET}")
samples_zip = Path("${SAMPLES_ZIP}")
depth_json = Path("${DEPTH_JSON}")
depth_frames = Path("${DEPTH_FRAMES}")

if not http_dir.is_dir():
    print(f"FAIL: missing HTTP dataset dir: {http_dir}", file=sys.stderr)
    sys.exit(1)

report = validate_viewer_publish(
    corpus,
    http_dataset_dir=http_dir,
    samples_zip=samples_zip if samples_zip.is_file() else None,
    depth_json=depth_json if depth_json.is_file() else None,
    depth_frames_dir=depth_frames if depth_frames.is_dir() else None,
    raise_on_error=True,
    datalab_root=Path("${DATALAB_ROOT}"),
    station="${STATION}",
    l2_publish=os.environ.get("EGO_VIEWER_PUBLISH_L2", "0").strip().lower() in ("1", "true", "yes"),
)
print(json.dumps(report, indent=2))
print("B7 viewer publish gate OK")
PY
