#!/usr/bin/env bash
# Provision ego-001 MCAP H.264 production on 130 (full stack alignment).
# - Capture: ecs-record-oak-mcap + ecs-oak-mcap-capture-stack.target
# - Mobile UI: ecs-ego-web → MCAP capture stack
# - Upload: ego-upload (MCAP) + station profile SSOT
# Usage: ego-130-provision-mcap-production.sh [ssh_target] [station_id]
# Production default: pilot rings only (130 never needs daily profile switching).
# Stress large rings: EGO_STRESS_CAPTURE=1 CAPTURE_PROFILE=long ego-130-provision-mcap-production.sh …
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
STATION_ID="${2:-ego-001}"
CAPTURE_PROFILE="${CAPTURE_PROFILE:-pilot}"
if [[ "${EGO_STRESS_CAPTURE:-0}" == "1" ]]; then
  CAPTURE_PROFILE="${CAPTURE_PROFILE:-long}"
fi
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT}/.." && pwd)"
CAPTURE_SRC="${ROOT}/ego-stream-client"
EGO_WEB_SRC="${ROOT}/ego-local-web"
REMOTE_STUDIO="/home/server/workspace/ego-studio"
REMOTE_CAPTURE="${REMOTE_STUDIO}/src/ego_capture_studio/capture"
REMOTE_CLI="${REMOTE_STUDIO}/src/ego_capture_studio/cli"
REMOTE_CONFIG="${REMOTE_STUDIO}/config"
CACHE_ROOT="/home/server/cache/${STATION_ID}"
CAPTURE_HOST="${TARGET#*@}"
PROFILE="${ROOT}/config/station-profiles/${STATION_ID}-mcap-production.env"

echo "==> Provision MCAP production (full align) ${TARGET} station=${STATION_ID} capture_profile=${CAPTURE_PROFILE}"

if [[ ! -f "$PROFILE" ]]; then
  echo "missing profile: $PROFILE" >&2
  exit 2
fi

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

_run_remote "mkdir -p ${REMOTE_CAPTURE} ${REMOTE_CLI} ${REMOTE_CONFIG} ${CACHE_ROOT}/segments ${CACHE_ROOT}/logs"

echo "==> Sync capture Python"
if rsync -av --delete --exclude '__pycache__' --exclude '*.pyc' "${CAPTURE_SRC}/" "${TARGET}:${REMOTE_CAPTURE}/" 2>/dev/null; then
  rsync -av "${CAPTURE_SRC}/cli/record_oak_stream.py" "${TARGET}:${REMOTE_CLI}/record_oak_stream.py"
  rsync -av "${CAPTURE_SRC}/cli/upload_segments.py" "${TARGET}:${REMOTE_CLI}/upload_segments.py"
  rsync -av "${CAPTURE_SRC}/cli/ego_upload.py" "${TARGET}:${REMOTE_CLI}/ego_upload.py"
else
  echo "rsync failed — using paramiko sftp (slower)"
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" TARGET="$TARGET" CAPTURE_SRC="$CAPTURE_SRC" REMOTE_CAPTURE="$REMOTE_CAPTURE" REMOTE_CLI="$REMOTE_CLI" python3 - <<'PY'
import os, paramiko
from pathlib import Path
target = os.environ["TARGET"]
user, _, host = target.partition("@")
src = Path(os.environ["CAPTURE_SRC"])
remote_capture = os.environ["REMOTE_CAPTURE"]
remote_cli = os.environ["REMOTE_CLI"]
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=20)
sftp = c.open_sftp()
for path in src.rglob("*"):
    if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
        continue
    rel = path.relative_to(src)
    remote = f"{remote_capture}/{rel}".replace("\\", "/")
    try:
        sftp.stat(str(path.parent).replace(str(src), remote_capture))
    except Exception:
        pass
    try:
        sftp.mkdir(os.path.dirname(remote))
    except Exception:
        pass
    sftp.put(str(path), remote)
for name in ("record_oak_stream.py", "upload_segments.py", "ego_upload.py"):
    sftp.put(str(src / "cli" / name), f"{remote_cli}/{name}")
sftp.close()
c.close()
print("capture sync ok")
PY
fi

echo "==> Install MCAP systemd + mobile UI drop-in + station env"
_scp "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap.service" "/tmp/ecs-record-oak-mcap.service"
_scp "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap.service.d/z-mcap-production.conf" "/tmp/z-mcap-production.conf"
if [[ "${CAPTURE_PROFILE}" == "long" ]]; then
  _scp "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap.service.d/z-mcap-long-capture.conf" "/tmp/z-mcap-long-capture.conf"
fi
_scp "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap.service.d/capture-stack.conf" "/tmp/z-mcap-capture-stack.conf"
_scp "${CAPTURE_SRC}/systemd/ecs-record-oak-mcap.service.d/z-oak-boot-pre.conf" "/tmp/z-oak-boot-pre.conf"
_scp "${CAPTURE_SRC}/systemd/ecs-oak-mcap-capture-stack.target" "/tmp/ecs-oak-mcap-capture-stack.target"
_scp "${CAPTURE_SRC}/systemd/ecs-oak-standby-stack.target" "/tmp/ecs-oak-standby-stack.target"
_scp "${EGO_WEB_SRC}/systemd/ecs-ego-web.service.d/z-mcap-production.conf" "/tmp/z-ego-web-mcap-production.conf"
_scp "${PROFILE}" "/tmp/ego-mcap-production.env"
_scp "${REPO_ROOT}/data-lab-platform/scripts/ego-upload-station.sh" "/tmp/ego-upload-station.sh"

_run_remote "$(cat <<REMOTE
set -euo pipefail
STATION_ID="${STATION_ID}"
CACHE_ROOT="${CACHE_ROOT}"

mkdir -p "\$HOME/.config/systemd/user/ecs-record-oak-mcap.service.d"
mkdir -p "\$HOME/.config/systemd/user/ecs-ego-web.service.d"
mkdir -p "\$HOME/.config/ego-station.env.d"
mkdir -p "\$HOME/.local/bin"

cp /tmp/ecs-record-oak-mcap.service "\$HOME/.config/systemd/user/"
cp /tmp/z-mcap-production.conf "\$HOME/.config/systemd/user/ecs-record-oak-mcap.service.d/z-mcap-production.conf"
CAPTURE_PROFILE="${CAPTURE_PROFILE}"
if [[ "\${CAPTURE_PROFILE}" == "long" ]] && [[ -f /tmp/z-mcap-long-capture.conf ]]; then
  cp /tmp/z-mcap-long-capture.conf "\$HOME/.config/systemd/user/ecs-record-oak-mcap.service.d/z-mcap-long-capture.conf"
  echo "capture profile: long (stress-only rings)"
else
  rm -f "\$HOME/.config/systemd/user/ecs-record-oak-mcap.service.d/z-mcap-long-capture.conf"
  rm -f /tmp/z-mcap-long-capture.conf
  echo "capture profile: pilot (2026-08-18 stable rings)"
fi
cp /tmp/z-mcap-capture-stack.conf "\$HOME/.config/systemd/user/ecs-record-oak-mcap.service.d/capture-stack.conf"
cp /tmp/z-oak-boot-pre.conf "\$HOME/.config/systemd/user/ecs-record-oak-mcap.service.d/z-oak-boot-pre.conf"
# Legacy pilot drop-ins override z-mcap-production (zz-* wins alphabetically → 80s cap).
rm -f "\$HOME/.config/systemd/user/ecs-record-oak-mcap.service.d/z-episode-seconds.conf"
rm -f "\$HOME/.config/systemd/user/ecs-record-oak-mcap.service.d/zz-episode-seconds.conf"
cp /tmp/ecs-oak-mcap-capture-stack.target "\$HOME/.config/systemd/user/"
cp /tmp/ecs-oak-standby-stack.target "\$HOME/.config/systemd/user/"
cp /tmp/z-ego-web-mcap-production.conf "\$HOME/.config/systemd/user/ecs-ego-web.service.d/z-mcap-production.conf"

{
  echo "# generated by ego-130-provision-mcap-production.sh — do not hand-edit"
  grep -v '^#' /tmp/ego-mcap-production.env | grep -v '^[[:space:]]*$'
} > "\$HOME/.config/ego-station.env.d/station.conf"
# Capture-only preview vars belong in z-mcap-production.conf (EnvironmentFile overrides drop-ins).
sed -i '/^OAK_HW_PREVIEW_H264=/d; /^PREVIEW_MAX_EDGE=/d; /^PREVIEW_FPS=/d' \
  "\$HOME/.config/ego-station.env.d/station.conf"

CK="\${CACHE_ROOT}/checkpoint.json"
cat > "\$HOME/.config/ego-station.env" <<EOF
EGO_STATION_ID=\${STATION_ID}
EGO_SEGMENT_ROOT=\${CACHE_ROOT}/segments
EGO_EXPORT_ROOT=/home/server/export/\${STATION_ID}
EGO_CAPTURE_CHECKPOINT=\${CK}
EGO_UPLOAD_LOG_DIR=\${CACHE_ROOT}/logs
DATALAB_HEARTBEAT_URL=http://10.10.10.34:8080/lerobot/api/collection/stations/\${STATION_ID}/upload
EGO_UPLOAD_URL=http://10.10.10.34:8080/lerobot/api/collection/stations/\${STATION_ID}/upload
STATION_UPLOAD_TOKEN=dl-upload-\${STATION_ID}-v1
DATALAB_CAPTURE_HOST=${CAPTURE_HOST}
EGO_SEGMENT_DELETE_AFTER_UPLOAD=1
EGO_NOTIFY_PROCESS=1
EGO_UPLOAD_UNTIL_COMPLETE=1
UPLOAD_PROTOCOL=mcap
SEGMENT_MCAP=1
SEGMENT_FRAME_BIN=0
EOF

printf 'EGO_UPLOAD_MODE=production\\n' > "\$HOME/.config/ego-station.env.d/upload-mode.conf"

install -m 0755 /tmp/ego-upload-station.sh "\$HOME/.local/bin/ego-upload"
echo '1' | sudo -S install -m 0755 /tmp/ego-upload-station.sh /usr/local/bin/ego-upload 2>/dev/null \
  || cp /tmp/ego-upload-station.sh /usr/local/bin/ego-upload 2>/dev/null || true

# Stop legacy JPEG capture path; align to MCAP stack only.
systemctl --user stop ecs-oak-capture-stack.target 2>/dev/null || true
systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
systemctl --user stop ecs-record-oak-mcap.service 2>/dev/null || true
systemctl --user disable ecs-record-oak-stream.service 2>/dev/null || true
systemctl --user disable ecs-record-oak-mcap-pilot.service 2>/dev/null || true
systemctl --user disable ecs-record-oak-mcap-track2.service 2>/dev/null || true
systemctl --user stop ecs-preview-standby.service 2>/dev/null || true

# Single checkpoint (archive legacy split-brain path).
if [[ -f "\${CACHE_ROOT}/segments/checkpoint.json" && ! -f "\${CK}" ]]; then
  mv "\${CACHE_ROOT}/segments/checkpoint.json" "\${CK}"
elif [[ -f "\${CACHE_ROOT}/segments/checkpoint.json" && -f "\${CK}" ]]; then
  mv "\${CACHE_ROOT}/segments/checkpoint.json" "\${CACHE_ROOT}/segments/checkpoint.json.bak.\$(date +%Y%m%d%H%M%S)"
fi

systemctl --user daemon-reload
# Capture is manual-only (phone UI). Never enable for boot — avoids post-reboot auto-record + beep.
systemctl --user stop ecs-oak-mcap-capture-stack.target 2>/dev/null || true
systemctl --user disable ecs-record-oak-mcap.service 2>/dev/null || true
systemctl --user disable ecs-oak-mcap-capture-stack.target 2>/dev/null || true
systemctl --user restart ecs-ego-web.service

echo "OK: MCAP stack aligned"
echo "  capture target: ecs-oak-mcap-capture-stack.target (disabled at boot)"
echo "  record unit:    ecs-record-oak-mcap.service (disabled at boot)"
echo "  start capture:  phone UI :8080, or: systemctl --user start ecs-oak-mcap-capture-stack.target"
echo "  checkpoint:     \${CK}"
REMOTE
)"

echo "Done."
echo "==> Hotspot watchdog (AX201 AP recovery)"
bash "${ROOT}/scripts/ego-130-provision-hotspot-watchdog.sh" "${TARGET}" || \
  echo "WARN: hotspot watchdog provision failed (run manually: ego-130-provision-hotspot-watchdog.sh ${TARGET})"
echo "Verify: bash data-lab-platform/scripts/ego-station-doctor.sh ${STATION_ID} ${TARGET}"
echo "Release: bash data-lab-platform/scripts/ego-release-check.sh ${STATION_ID} ${TARGET}"
echo "Record: phone UI :8080 (capture disabled at boot; manual start only)"
