#!/usr/bin/env bash
# One-click 130 update for v0.2.0-rectified-720p-l2.
# 130: 1280×800 MCAP passthrough (no edge crop). 34: rectify → 1280×720 corpus.
# Wraps ego-130-provision-mcap-production.sh with release tag verification.
#
# Usage: RC_CAPTURE_PASS=1 ego-130-provision-720p-l2.sh [ssh_target] [station_id]
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
STATION_ID="${2:-ego-001}"
TAG="v0.2.0-rectified-720p-l2"
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PROFILE="${ROOT}/data-lab-platform/config/station-profiles/${STATION_ID}-mcap-production.env"
OAK_CAPTURE="${ROOT}/data-lab-platform/ego-stream-client/oak_4p_capture.py"

echo "==> ego-130-provision-720p-l2 tag=${TAG} target=${TARGET} station=${STATION_ID}"

grep -q '^EGO_OAK_MODE=rectify' "$PROFILE" || {
  echo "profile missing EGO_OAK_MODE=rectify: $PROFILE" >&2
  exit 2
}
grep -q 'OAK_DEFAULT_FRAME_HEIGHT = 800' "${ROOT}/data-lab-platform/ego-stream-client/ego_spec.py" \
  || grep -q '^OAK_DEFAULT_FRAME_HEIGHT=800' "${ROOT}/data-lab-platform/ego-stream-client/ego_spec.py" || {
  echo "ego_spec.py must keep 1280x800 passthrough on 130 (no edge crop)" >&2
  exit 2
}
grep -q 'crop_from=' "$OAK_CAPTURE" && {
  echo "oak_4p_capture.py must not use device-side crop_from (720p crop is rectify-only)" >&2
  exit 2
}

bash "${SCRIPT_DIR}/ego-130-provision-mcap-production.sh" "${TARGET}" "${STATION_ID}"

VERIFY=$(cat <<REMOTE
set -euo pipefail
python3 - <<'PY'
import importlib.util
from pathlib import Path

spec_path = Path.home() / "workspace/ego-studio/src/ego_capture_studio/capture/ego_spec.py"
spec = importlib.util.spec_from_file_location("ego_spec", spec_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert int(mod.OAK_DEFAULT_FRAME_HEIGHT) == 800, mod.OAK_DEFAULT_FRAME_HEIGHT
assert int(mod.OAK_DEFAULT_FRAME_WIDTH) == 1280, mod.OAK_DEFAULT_FRAME_WIDTH
print("130 ego_spec ok:", mod.OAK_DEFAULT_FRAME_WIDTH, "x", mod.OAK_DEFAULT_FRAME_HEIGHT)

capture_path = Path.home() / "workspace/ego-studio/src/ego_capture_studio/capture/oak_4p_capture.py"
text = capture_path.read_text()
assert "crop_from=" not in text, "device-side crop still present"
print("130 oak_4p_capture passthrough ok (no edge crop)")
PY
REMOTE
)

if [[ -n "${RC_CAPTURE_PASS:-}" ]]; then
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS}" TARGET="$TARGET" REMOTE_BODY="$VERIFY" REMOTE_TIMEOUT=120 python3 - <<'PY'
import os, paramiko
target = os.environ["TARGET"]
user, _, host = target.partition("@")
body = os.environ["REMOTE_BODY"]
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=30)
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{body}\nREMOTE", timeout=120)
print((o.read() + e.read()).decode(), end="")
raise SystemExit(o.channel.recv_exit_status())
PY
else
  ssh -o BatchMode=yes "${TARGET}" "bash -s" <<< "$VERIFY"
fi

echo "==> 130 provision complete (${TAG}, capture 1280x800 passthrough)"
