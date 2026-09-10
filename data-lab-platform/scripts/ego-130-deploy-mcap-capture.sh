#!/usr/bin/env bash
# Pin and deploy ego-stream-client capture to ego-001 (130) from git — not ad-hoc scp.
#
# 1. Export ego-stream-client at GIT_REF into a versioned tarball + manifest
# 2. Upload tarball to 130 backup dir and extract into ego-studio capture tree
# 3. Install MCAP systemd units + station profile (same as production provision)
#
# Usage:
#   ego-130-deploy-mcap-capture.sh [git_ref] [ssh_target] [station_id]
#
# Examples:
#   ego-130-deploy-mcap-capture.sh deploy-release
#   ego-130-deploy-mcap-capture.sh ego-001-prod-snapshot-20260910-1534
#
# After deploy, tag the repo (if not already tagged):
#   git tag -a ego-001-prod-snapshot-YYYYMMDD-HHMM -m "..."
set -euo pipefail

GIT_REF="${1:-HEAD}"
TARGET="${2:-server@10.10.10.130}"
STATION_ID="${3:-ego-001}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${ROOT}/.." && pwd)"
CAPTURE_REL="data-lab-platform/ego-stream-client"
BACKUP_DIR="${REPO_ROOT}/data-storage/backups/ego-001-deploy"
STAMP="$(date +%Y%m%d-%H%M%S)"
SNAPSHOT_TAG="${EGO_DEPLOY_TAG:-ego-001-prod-snapshot-${STAMP}}"

cd "${REPO_ROOT}"
if ! git rev-parse --verify "${GIT_REF}^{commit}" >/dev/null 2>&1; then
  echo "unknown git ref: ${GIT_REF}" >&2
  exit 2
fi
GIT_SHA="$(git rev-parse "${GIT_REF}^{commit}")"
SHORT_SHA="$(git rev-parse --short "${GIT_REF}^{commit}")"

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

echo "==> Export capture @ ${GIT_REF} (${SHORT_SHA})"
git archive --format=tar "${GIT_REF}" "${CAPTURE_REL}" | tar -x -C "${WORK}"
CAPTURE_SRC="${WORK}/${CAPTURE_REL}"
if [[ ! -d "${CAPTURE_SRC}" ]]; then
  echo "export missing ${CAPTURE_REL}" >&2
  exit 2
fi

TARBALL="${BACKUP_DIR}/${SNAPSHOT_TAG}.tar.gz"
MANIFEST="${BACKUP_DIR}/${SNAPSHOT_TAG}.manifest.json"
mkdir -p "${BACKUP_DIR}"

(
  cd "${WORK}"
  tar -czf "${TARBALL}" "${CAPTURE_REL}"
)
SHA256="$(sha256sum "${TARBALL}" | awk '{print $1}')"

python3 - <<PY
import json
from pathlib import Path
manifest = {
    "tag": "${SNAPSHOT_TAG}",
    "git_ref": "${GIT_REF}",
    "commit": "${GIT_SHA}",
    "short_sha": "${SHORT_SHA}",
    "created": "${STAMP}",
    "station_id": "${STATION_ID}",
    "ssh_target": "${TARGET}",
    "capture_path": "ego-stream-client/",
    "tarball": str(Path(${TARBALL@Q})),
    "sha256": "${SHA256}",
    "deploy_script": "data-lab-platform/scripts/ego-130-deploy-mcap-capture.sh",
}
Path(${MANIFEST@Q}).write_text(json.dumps(manifest, indent=2) + "\\n")
print(json.dumps(manifest, indent=2))
PY

echo "==> Local archive: ${TARBALL} ($(du -h "${TARBALL}" | cut -f1))"
echo "==> Manifest:      ${MANIFEST}"

REMOTE_BACKUP="/home/server/backup"
REMOTE_TARBALL="${REMOTE_BACKUP}/${SNAPSHOT_TAG}.tar.gz"
REMOTE_MANIFEST="${REMOTE_BACKUP}/${SNAPSHOT_TAG}.manifest.json"
REMOTE_STUDIO="/home/server/workspace/ego-studio"
REMOTE_CAPTURE="${REMOTE_STUDIO}/src/ego_capture_studio/capture"
REMOTE_CLI="${REMOTE_STUDIO}/src/ego_capture_studio/cli"

_run_remote() {
  local script="$1"
  if ssh -o BatchMode=yes -o ConnectTimeout=8 "${TARGET}" "bash -s" <<< "$script"; then
    return 0
  fi
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" TARGET="$TARGET" REMOTE_BODY="$script" python3 - <<'PY'
import os, paramiko
target = os.environ["TARGET"]
user, _, host = target.partition("@")
body = os.environ["REMOTE_BODY"]
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=20)
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{body}\nREMOTE", timeout=300)
out = (o.read() + e.read()).decode()
print(out, end="")
raise SystemExit(o.channel.recv_exit_status())
PY
}

_scp() {
  local src="$1" dst="$2"
  if scp -q -o BatchMode=yes -o ConnectTimeout=8 "$src" "${TARGET}:${dst}" 2>/dev/null; then
    return 0
  fi
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" TARGET="$TARGET" SCP_SRC="$src" SCP_DST="$dst" python3 - <<'PY'
import os, paramiko
target = os.environ["TARGET"]
user, _, host = target.partition("@")
src = os.environ["SCP_SRC"]
dst = os.environ["SCP_DST"]
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=20)
c.open_sftp().put(src, dst)
c.close()
PY
}

echo "==> Upload tarball + manifest to ${TARGET}"
_run_remote "mkdir -p ${REMOTE_BACKUP} ${REMOTE_CAPTURE} ${REMOTE_CLI}"
_scp "${TARBALL}" "${REMOTE_TARBALL}"
_scp "${MANIFEST}" "${REMOTE_MANIFEST}"

echo "==> Extract capture tree on 130"
_run_remote "$(cat <<REMOTE
set -euo pipefail
cd /tmp
rm -rf ego-stream-client-deploy
mkdir -p ego-stream-client-deploy
tar -xzf ${REMOTE_TARBALL} -C ego-stream-client-deploy
rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' \
  ego-stream-client-deploy/${CAPTURE_REL}/ ${REMOTE_CAPTURE}/
install -m 0644 ego-stream-client-deploy/${CAPTURE_REL}/cli/record_oak_stream.py ${REMOTE_CLI}/record_oak_stream.py
install -m 0644 ego-stream-client-deploy/${CAPTURE_REL}/cli/upload_segments.py ${REMOTE_CLI}/upload_segments.py
rm -rf ego-stream-client-deploy
echo "capture extracted: ${REMOTE_CAPTURE} @ ${GIT_SHA}"
REMOTE
)"

echo "==> Install systemd + station profile (production align)"
bash "${SCRIPT_DIR}/ego-130-provision-mcap-production.sh" "${TARGET}" "${STATION_ID}"

echo ""
echo "==> Deploy complete"
echo "  tag:      ${SNAPSHOT_TAG}"
echo "  commit:   ${GIT_SHA}"
echo "  tarball:  ${TARBALL}"
echo "  remote:   ${REMOTE_TARBALL}"
echo ""
echo "Verify: bash data-lab-platform/scripts/ego-station-doctor.sh ${STATION_ID} ${TARGET}"
