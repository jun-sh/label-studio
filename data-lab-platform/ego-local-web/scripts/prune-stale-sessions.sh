#!/usr/bin/env bash
# Remove all session dirs with no seg_* under ego-001 segments (edge cleanup).
# Safe when no pending upload segments exist on disk.
set -euo pipefail

SEG_ROOT="${EGO_SEGMENT_ROOT:-/home/server/cache/ego-001/segments}"
SESSIONS="${SEG_ROOT}/sessions"

if [[ ! -d "$SESSIONS" ]]; then
  echo "No sessions dir: $SESSIONS"
  exit 0
fi

pending="$(find "$SESSIONS" -type d -name 'seg_*' 2>/dev/null | wc -l | tr -d ' ')"
if [[ "$pending" != "0" ]]; then
  echo "REFUSE: found $pending seg_* dirs on disk — upload or review first."
  exit 1
fi

count=0
for sess in "$SESSIONS"/sess_*; do
  [[ -d "$sess" ]] || continue
  rm -rf "$sess"
  count=$((count + 1))
done

rm -f "${SEG_ROOT}/checkpoint.json" "${SEG_ROOT}/strict_emit_ts.json"
if [[ -f "${SEG_ROOT}/registry.json" ]]; then
  printf '%s\n' '{"version":2,"sessions":{}}' > "${SEG_ROOT}/registry.json"
fi

echo "Removed $count session dir(s); cleared checkpoint and registry."
