#!/usr/bin/env bash
# Pull station code from ego-130 into this repo (130 is source of truth).
# Usage: RC_CAPTURE_PASS=1 bash ego-130-pull-from-station.sh [server@10.10.10.130]
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB="${ROOT}/ego-local-web"
CAPTURE="${ROOT}/ego-stream-client"
REMOTE_WEB="/home/server/ego-web"
REMOTE_STAGING="/home/server/ego-web-staging"
REMOTE_DEV="/home/server/ego-web-dev"
REMOTE_CAPTURE="/home/server/workspace/ego-studio/src/ego_capture_studio/capture"
REMOTE_CLI="/home/server/workspace/ego-studio/src/ego_capture_studio/cli"
REMOTE_SYSTEMD="/home/server/.config/systemd/user"

SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=15)
if [[ -n "${SSHPASS:-}" ]] || [[ -n "${RC_CAPTURE_PASS:-}" ]]; then
  export SSHPASS="${SSHPASS:-${RC_CAPTURE_PASS:-1}}"
  RSYNC_RSH="sshpass -e ssh ${SSH_OPTS[*]}"
  SCP=(sshpass -e scp "${SSH_OPTS[@]}")
else
  RSYNC_RSH="ssh ${SSH_OPTS[*]}"
  SCP=(scp "${SSH_OPTS[@]}")
fi

_rsync_from() {
  local remote_path="$1"
  local local_path="$2"
  rsync -av --delete -e "$RSYNC_RSH" "${TARGET}:${remote_path}" "${local_path}"
}

_pull_file() {
  local remote="$1"
  local local="$2"
  mkdir -p "$(dirname "$local")"
  "${SCP[@]}" "${TARGET}:${remote}" "$local"
}

echo "==> Pull from ${TARGET} into ${ROOT}"

echo "==> Production ego-web (8080)"
for rel in \
  ego_web.py ego_tcp_bridge.py export_offline.py README.md field-export-and-import.md \
  docs/ego-app-tcp-protocol.md templates/capture_ui.html \
  tools/ego_tcp_client_test.py tools/ego_tcp_protocol_acceptance.py; do
  _pull_file "${REMOTE_WEB}/${rel}" "${WEB}/${rel}"
done

# Flattened on station → repo scripts/
declare -A SCRIPT_MAP=(
  ["ego-hotspot-up.sh"]="scripts/ego-hotspot-up.sh"
  ["ego-hotspot-down.sh"]="scripts/ego-hotspot-down.sh"
  ["ego-hotspot-boot-wait.sh"]="scripts/ego-hotspot-boot-wait.sh"
  ["ego-hotspot-watchdog.sh"]="scripts/ego-hotspot-watchdog.sh"
  ["ego-hotspot-client-watch.sh"]="scripts/ego-hotspot-client-watch.sh"
  ["ego-usb-ap-bind.sh"]="scripts/ego-usb-ap-bind.sh"
  ["ego-usb-ap-resolve.sh"]="scripts/ego-usb-ap-resolve.sh"
  ["ego-wlo1-wifi-watchdog.sh"]="scripts/ego-wlo1-wifi-watchdog.sh"
  ["export-offline.sh"]="scripts/export-offline.sh"
  ["install-ego-cli.sh"]="scripts/install-ego-cli.sh"
  ["ego-export"]="scripts/ego-export"
  ["ego-upload"]="scripts/ego-upload"
  ["ego-derive-status"]="scripts/ego-derive-status"
  ["scripts/prune-stale-sessions.sh"]="scripts/prune-stale-sessions.sh"
)
for remote_name in "${!SCRIPT_MAP[@]}"; do
  local_rel="${SCRIPT_MAP[$remote_name]}"
  if sshpass -e ssh "${SSH_OPTS[@]}" "${TARGET}" "test -f ${REMOTE_WEB}/${remote_name}" 2>/dev/null; then
    _pull_file "${REMOTE_WEB}/${remote_name}" "${WEB}/${local_rel}"
    chmod +x "${WEB}/${local_rel}" 2>/dev/null || true
  fi
done

echo "==> static/"
_rsync_from "${REMOTE_WEB}/static/" "${WEB}/static/"

echo "==> Staging quad preview (8081)"
_pull_file "${REMOTE_STAGING}/templates/quad_preview.html" "${WEB}/templates/quad_preview.html"

echo "==> Dev change_view test page (8082)"
if sshpass -e ssh "${SSH_OPTS[@]}" "${TARGET}" "test -f ${REMOTE_DEV}/templates/change_view_test.html" 2>/dev/null; then
  _pull_file "${REMOTE_DEV}/templates/change_view_test.html" "${WEB}/templates/change_view_test.html"
fi

echo "==> Capture stack (ego-studio → ego-stream-client)"
shopt -s nullglob
for local_py in "${CAPTURE}"/*.py; do
  base="$(basename "$local_py")"
  if sshpass -e ssh "${SSH_OPTS[@]}" "${TARGET}" "test -f ${REMOTE_CAPTURE}/${base}" 2>/dev/null; then
    _pull_file "${REMOTE_CAPTURE}/${base}" "${CAPTURE}/${base}"
  fi
done
for local_py in "${CAPTURE}"/cli/*.py; do
  base="$(basename "$local_py")"
  if sshpass -e ssh "${SSH_OPTS[@]}" "${TARGET}" "test -f ${REMOTE_CLI}/${base}" 2>/dev/null; then
    _pull_file "${REMOTE_CLI}/${base}" "${CAPTURE}/cli/${base}"
  fi
done
shopt -u nullglob

echo "==> systemd drop-ins (station user config → repo)"
for pair in \
  "ecs-ego-web.service.d/z-mcap-production.conf:${WEB}/systemd/ecs-ego-web.service.d/z-mcap-production.conf" \
  "ecs-ego-tcp-bridge.service.d/z-mcap-production.conf:${WEB}/systemd/ecs-ego-tcp-bridge.service.d/z-mcap-production.conf" \
  "ecs-record-oak-mcap.service.d/z-mcap-production.conf:${CAPTURE}/systemd/ecs-record-oak-mcap.service.d/z-mcap-production.conf"; do
  remote_rel="${pair%%:*}"
  local_path="${pair##*:}"
  remote_path="${REMOTE_SYSTEMD}/${remote_rel}"
  if sshpass -e ssh "${SSH_OPTS[@]}" "${TARGET}" "test -f ${remote_path}" 2>/dev/null; then
    _pull_file "${remote_path}" "${local_path}"
  else
    echo "WARN: missing on station: ${remote_path}"
  fi
done

echo "==> Verify key hashes match station"
python3 - <<PY
import hashlib, os, paramiko, sys
target = "${TARGET}"
user, _, host = target.partition("@")
password = os.environ.get("RC_CAPTURE_PASS") or os.environ.get("SSHPASS") or "1"
pairs = [
    ("${WEB}/ego_web.py", "${REMOTE_WEB}/ego_web.py"),
    ("${CAPTURE}/record_oak_stream.py", "${REMOTE_CAPTURE}/record_oak_stream.py"),
    ("${WEB}/templates/quad_preview.html", "${REMOTE_STAGING}/templates/quad_preview.html"),
]
def md5_local(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1<<20), b""): h.update(c)
    return h.hexdigest()
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=password, timeout=20)
sftp = c.open_sftp()
ok = True
for local, remote in pairs:
    if not os.path.isfile(local):
        print(f"MISSING local {local}")
        ok = False
        continue
    h = hashlib.md5()
    with sftp.open(remote, "rb") as f:
        while True:
            b = f.read(1<<20)
            if not b: break
            h.update(b)
    mr = h.hexdigest()
    ml = md5_local(local)
    tag = "OK" if ml == mr else "MISMATCH"
    print(f"{tag} {os.path.basename(local)}")
    if ml != mr:
        ok = False
c.close()
sys.exit(0 if ok else 1)
PY

echo "==> Done. Review: git status data-lab-platform/ego-local-web data-lab-platform/ego-stream-client"
