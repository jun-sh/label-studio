#!/usr/bin/env bash
# Pre-download LeRobot Studio assets on the host (optional, speeds first container start).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../lerobot-studio/static" && pwd)"
mkdir -p "$ROOT"

export LEROBOT_STUDIO_ROOT="$ROOT"
export LEROBOT_STUDIO_UPSTREAM="${LEROBOT_STUDIO_UPSTREAM:-https://io-ai.tech}"

"$(dirname "$0")/../lerobot-studio/download-assets.sh"
echo "Assets saved to ${ROOT}"
