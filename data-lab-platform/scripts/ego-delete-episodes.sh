#!/usr/bin/env bash
# Delete specific collection episodes by sidebar # (episode_index).
# Preserves raw/segments. NOT for whole-station delete (ego-trash-station.sh).
#
# Usage:
#   bash ego-delete-episodes.sh ego-002 --index 12,31,34
#   bash ego-delete-episodes.sh ego-002 --index 12,31 --apply
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "${SCRIPT_DIR}/ego-delete-episodes.py" "$@"
