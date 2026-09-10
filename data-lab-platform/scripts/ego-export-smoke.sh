#!/usr/bin/env bash
# CI / ops smoke: export gate requires --order-manifest (R1 regression).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STATION="${RC_STATION:-ego-001}"
EXPORT_PY="${SCRIPT_DIR}/ego-export-delivery.py"
SESSIONS_PY="${SCRIPT_DIR}/ego-pipeline-sessions.py"

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "==> ego-export-smoke station=${STATION}"

set +e
python3 "$EXPORT_PY" "$STATION" --datalab-root "$DATALAB_ROOT" --gate-only 2>&1
rc=$?
set -e
if [[ "$rc" -eq 0 ]]; then
  fail "export gate should fail without --order-manifest"
fi
echo "OK: gate rejects missing --order-manifest (rc=${rc})"

mapfile -t EXPORTABLE < <(python3 "$SESSIONS_PY" exportable-sessions "$STATION" --datalab-root "$DATALAB_ROOT" 2>/dev/null || true)
if [[ ${#EXPORTABLE[@]} -eq 0 ]]; then
  echo "SKIP: no finalize.done sessions (run ego-process first)"
  exit 0
fi

ORDER_MANIFEST="${DATALAB_ROOT}/data-storage/ego-delivery/${STATION}/orders/ci-smoke-order.json"
python3 "$SESSIONS_PY" build-order-manifest "$STATION" --datalab-root "$DATALAB_ROOT" --output "$ORDER_MANIFEST" >/dev/null

EGO_EXPORT_REQUIRE_QC=0 EGO_EXPORT_REQUIRE_ANNOTATION=0 python3 "$EXPORT_PY" "$STATION" \
  --datalab-root "$DATALAB_ROOT" \
  --order-manifest "$ORDER_MANIFEST" \
  --gate-only \
  --no-require-qc >/tmp/ego-export-smoke-gate.json 2>&1 || true

if grep -q '"ok": true' /tmp/ego-export-smoke-gate.json 2>/dev/null; then
  echo "OK: export gate passed (${#EXPORTABLE[@]} sessions)"
  exit 0
fi

if grep -q '"blocked"' /tmp/ego-export-smoke-gate.json 2>/dev/null; then
  echo "OK: order manifest accepted; gate blocked on business rules (annotation/SLO) — R1 wiring verified"
  cat /tmp/ego-export-smoke-gate.json
  exit 0
fi

cat /tmp/ego-export-smoke-gate.json 2>/dev/null || true
fail "unexpected export gate response"
