#!/usr/bin/env bash
# ego-lerobot-purity-gate.sh — L2 corpus purity contract (features allowlist + no offline sidecars).
set -euo pipefail

SLUG="${1:-}"
STATION="${EGO_STATION:-ego-001}"
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SESSIONS_PY="${SCRIPT_DIR}/ego-pipeline-sessions.py"

if [[ -z "$SLUG" ]]; then
  SLUG="$(python3 "$SESSIONS_PY" slug "$STATION" --datalab-root "$DATALAB_ROOT" 2>/dev/null || echo "ego_001")"
fi

CORPUS="${DATALAB_ROOT}/data-storage/corpus/${SLUG}"
export PYTHONPATH="${DATALAB_ROOT}/../ego-platform/src:${PYTHONPATH:-}"

if [[ ! -d "$CORPUS" ]]; then
  echo "SKIP: corpus not found (${CORPUS}) — nothing to gate yet"
  exit 0
fi

python3 <<PY
import json
import sys
from pathlib import Path

from ego_platform.lerobot.purity_gate import validate_corpus_purity

corpus = Path("${CORPUS}")
report = validate_corpus_purity(corpus, raise_on_error=True)
print(json.dumps(report, indent=2))
print("L2 corpus purity gate OK")
PY
