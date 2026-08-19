#!/usr/bin/env bash
# DEPRECATED — ego production uses HW JPEG frame bins, not segment H264.
# Usage: ego-130-verify-h264.sh [ssh_target]
set -euo pipefail

echo "DEPRECATED: H264 verify replaced by ego-130-verify-production.sh" >&2
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "${ROOT}/scripts/ego-130-verify-production.sh" "${1:-server@10.10.10.130}"
