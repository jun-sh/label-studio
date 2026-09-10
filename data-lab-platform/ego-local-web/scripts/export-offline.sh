#!/usr/bin/env bash
# One-click offline export: pack pending segments -> export/ready/YYYYMMDD/
# Requires ego-studio venv (pack_segment_tar_zst). Does not touch capture stack code.
set -euo pipefail

EGO_WEB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EGO_STUDIO_ROOT="${EGO_STUDIO_ROOT:-/home/server/workspace/ego-studio}"
PY="${EGO_STUDIO_ROOT}/.venv/bin/python"
EXPORT_PY="${EGO_WEB_ROOT}/export_offline.py"

if [[ -f "$EXPORT_PY" ]]; then
  :
elif [[ -f "$(dirname "$EGO_WEB_ROOT")/export_offline.py" ]]; then
  # Running from repo scripts/ during development.
  EGO_WEB_ROOT="$(cd "$(dirname "$EGO_WEB_ROOT")" && pwd)"
  EXPORT_PY="${EGO_WEB_ROOT}/export_offline.py"
fi

if [[ ! -x "$PY" ]]; then
  echo "ERROR: ego-studio Python not found: $PY" >&2
  exit 2
fi
if [[ ! -f "$EXPORT_PY" ]]; then
  echo "ERROR: export_offline.py not found: $EXPORT_PY" >&2
  exit 2
fi

export EGO_SEGMENT_ROOT="${EGO_SEGMENT_ROOT:-/home/server/cache/ego-001/segments}"
export EGO_EXPORT_ROOT="${EGO_EXPORT_ROOT:-/home/server/export/ego-001}"
export EGO_CAPTURE_TARGET="${EGO_CAPTURE_TARGET:-ecs-oak-capture-stack.target}"
export EGO_EXPORT_DELETE_AFTER="${EGO_EXPORT_DELETE_AFTER:-1}"

exec "$PY" "$EXPORT_PY" "$@"
