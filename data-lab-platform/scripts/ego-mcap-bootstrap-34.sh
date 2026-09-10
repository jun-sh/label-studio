#!/usr/bin/env bash
# Bootstrap ego-001 MCAP stream meta (camera intrinsics, info.json scaffold).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATION="ego-001"
STREAM_ROOT="${ROOT}/data-storage/stream/${STATION}"

mkdir -p "${STREAM_ROOT}/meta"
if [[ ! -f "${STREAM_ROOT}/meta/info.json" ]]; then
  cat >"${STREAM_ROOT}/meta/info.json" <<'EOF'
{
  "codebase_version": "v3.0",
  "robot_type": "ego_oak_4cam",
  "total_episodes": 0,
  "total_frames": 0,
  "fps": 30,
  "asset_layers": {
    "raw_mcap": "sensors_only",
    "stream_lerobot": "preview_no_real_pose",
    "corpus": "convert_hand_pose"
  }
}
EOF
fi

echo "[bootstrap] ${STATION} stream root ready at ${STREAM_ROOT}"
