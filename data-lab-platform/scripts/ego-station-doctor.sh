#!/usr/bin/env bash
# ego-station-doctor — SSH health checks for 130 capture + 34 stream markers.
# Usage: ego-station-doctor.sh [station] [ssh_target]
set -euo pipefail

STATION="${1:-ego-001}"
TARGET="${2:-server@10.10.10.130}"

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
DATALAB_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PROFILE_FILE="${DATALAB_ROOT}/data-lab-platform/config/station-profiles/${STATION}-mcap-production.env"
SESSIONS_PY="${SCRIPT_DIR}/ego-pipeline-sessions.py"

fail=0
die() { echo "FAIL: $*" >&2; fail=1; }
ok() { echo "OK: $*"; }

echo "==> ego-station-doctor station=${STATION} target=${TARGET}"

if [[ -f "$PROFILE_FILE" ]]; then
  ok "profile present (${PROFILE_FILE})"
else
  die "missing profile ${PROFILE_FILE}"
fi

REMOTE_SCRIPT='set -euo pipefail
fail=0
die() { echo "FAIL: $*"; fail=1; }
ok() { echo "OK: $*"; }
conf="$HOME/.config/ego-station.env.d/station.conf"
if [[ ! -f "$conf" ]]; then die "missing $conf"; else
  grep -q "^OAK_H264=1" "$conf" || die "station.conf OAK_H264!=1"
  grep -q "^OAK_HW_JPEG=0" "$conf" || die "station.conf OAK_HW_JPEG!=0"
  grep -q "^UPLOAD_PROTOCOL=mcap" "$conf" || die "station.conf UPLOAD_PROTOCOL!=mcap"
  grep -q "^SEGMENT_FRAME_BIN=0" "$conf" || die "station.conf SEGMENT_FRAME_BIN!=0"
  if grep -q "^OAK_HW_PREVIEW_H264=0" "$conf" 2>/dev/null; then
    die "station.conf OAK_HW_PREVIEW_H264=0 disables live preview (remove line; use z-mcap-production.conf)"
  fi
  ok "station.conf MCAP H.264 profile"
fi
if lsusb 2>/dev/null | grep -q "03e7:f63b"; then
  ok "OAK runtime 03e7:f63b"
elif lsusb 2>/dev/null | grep -q "03e7:f63c"; then
  oak_py="$HOME/workspace/ego-studio/.venv/bin/python"
  if [[ -x "$oak_py" ]]; then
    (cd "$HOME/workspace/ego-studio" && PYTHONPATH="$HOME/workspace/ego-studio/src" \
      "$oak_py" -m ego_capture_studio.tools.oak_boot_from_bootloader >/dev/null 2>&1) || true
  fi
  # Myriad X often keeps USB PID f63c while DepthAI pipeline is usable; MCAP ExecStartPre re-boots.
  ok "OAK USB present 03e7:f63c (Luxonis bootloader PID; MCAP capture pre-boot enabled)"
elif lsusb 2>/dev/null | grep -q "03e7:"; then
  ok "OAK USB present"
else
  die "OAK USB missing"
fi
systemctl --user is-active ecs-preview-standby.service >/dev/null 2>&1 && die "preview standby active" || ok "preview standby inactive"
systemctl --user cat ecs-record-oak-mcap.service >/dev/null 2>&1 && ok "ecs-record-oak-mcap installed" || die "ecs-record-oak-mcap missing"
dropin="$HOME/.config/systemd/user/ecs-record-oak-mcap.service.d/z-mcap-production.conf"
[[ -f "$dropin" ]] && ok "z-mcap-production.conf" || die "missing z-mcap-production.conf"
grep -q "^Environment=OAK_HW_PREVIEW_H264=1" "$dropin" \
  || die "z-mcap-production.conf missing OAK_HW_PREVIEW_H264=1"
ok "z-mcap-production preview enabled"
if systemctl --user is-active ecs-record-oak-mcap.service >/dev/null 2>&1; then
  pid="$(systemctl --user show ecs-record-oak-mcap.service -p MainPID --value 2>/dev/null || true)"
  if [[ -n "$pid" && "$pid" != "0" && -r "/proc/${pid}/environ" ]]; then
    preview_env="$(tr '\0' '\n' < "/proc/${pid}/environ" | grep '^OAK_HW_PREVIEW_H264=' || true)"
    [[ "$preview_env" == "OAK_HW_PREVIEW_H264=1" ]] \
      || die "capture process ${preview_env:-unset} (expected =1; delete from station.conf)"
    ok "capture process OAK_HW_PREVIEW_H264=1"
    preview_code="$(curl -sf -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8765/preview/front_left/jpg 2>/dev/null || echo 000)"
    case "$preview_code" in
      200) ok "preview HTTP 200" ;;
      503) ok "preview HTTP 503 (warming or post-stop)" ;;
      000) die "preview HTTP unreachable on :8765" ;;
      *) die "preview HTTP ${preview_code}" ;;
    esac
  else
    die "capture active but MainPID missing"
  fi
else
  ok "capture idle (skip live preview env/HTTP check)"
fi
mcap_stack="$HOME/.config/systemd/user/ecs-oak-mcap-capture-stack.target"
[[ -f "$mcap_stack" ]] && ok "ecs-oak-mcap-capture-stack.target" || die "missing ecs-oak-mcap-capture-stack.target"
web_dropin="$HOME/.config/systemd/user/ecs-ego-web.service.d/z-mcap-production.conf"
if [[ -f "$web_dropin" ]]; then
  grep -q "ecs-oak-mcap-capture-stack.target" "$web_dropin" || die "ego-web drop-in wrong capture target"
  grep -q "ecs-record-oak-mcap.service" "$web_dropin" || die "ego-web drop-in wrong record unit"
  ok "ego-web MCAP capture drop-in"
else
  die "missing ego-web z-mcap-production.conf"
fi
web_env="$(systemctl --user show ecs-ego-web.service -p Environment --no-pager 2>/dev/null || true)"
echo "$web_env" | tr " " "\n" | grep -q "EGO_CAPTURE_TARGET=ecs-oak-mcap-capture-stack.target" \
  || die "ecs-ego-web not using MCAP capture target (daemon-reload + restart?)"
ok "ecs-ego-web MCAP capture target"
ck="$HOME/.config/ego-station.env"
grep -q "^EGO_CAPTURE_CHECKPOINT=.*/checkpoint.json" "$ck" 2>/dev/null \
  || die "ego-station.env checkpoint not unified"
grep -q "segments/checkpoint.json" "$ck" 2>/dev/null && die "ego-station.env still uses segments/checkpoint.json"
ok "unified checkpoint path"
stream_state="$(systemctl --user is-enabled ecs-record-oak-stream.service 2>/dev/null || true)"
[[ "$stream_state" == "disabled" || "$stream_state" == "masked" ]] && ok "stream capture disabled" \
  || die "ecs-record-oak-stream still enabled (${stream_state:-unknown})"
command -v ego-upload >/dev/null && ego-upload --help 2>&1 | grep -q notify && ok "ego-upload --notify" || die "ego-upload broken"
if command -v ego-upload >/dev/null; then
  ego_upload_bin="$(command -v ego-upload)"
  grep -q "_sync_segment_store_env" "$ego_upload_bin" 2>/dev/null && ok "ego-upload format autodetect" \
    || die "ego-upload outdated (missing _sync_segment_store_env)"
fi
exit "$fail"'

echo "==> 130 checks"
if ssh -o BatchMode=yes -o ConnectTimeout=8 "${TARGET}" "bash -s" <<< "$REMOTE_SCRIPT"; then
  :
else
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" TARGET="$TARGET" REMOTE_SCRIPT="$REMOTE_SCRIPT" python3 - <<'PY' || fail=1
import os, paramiko
target = os.environ["TARGET"]
user, _, host = target.partition("@")
script = os.environ["REMOTE_SCRIPT"]
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=20)
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{script}\nREMOTE", timeout=120)
out = (o.read() + e.read()).decode()
print(out, end="")
rc = o.channel.recv_exit_status()
c.close()
raise SystemExit(rc)
PY
fi

echo "==> 34 stream markers"
if python3 "$SESSIONS_PY" has-data "$STATION" --datalab-root "$DATALAB_ROOT" 2>/dev/null; then
  ready_n=$(python3 "$SESSIONS_PY" ready-sessions "$STATION" --datalab-root "$DATALAB_ROOT" 2>/dev/null | wc -l)
  ok "stream has data (ready_sessions=${ready_n})"
else
  ok "stream empty (greenfield)"
fi

if python3 "$SESSIONS_PY" doctor "$STATION" --datalab-root "$DATALAB_ROOT" 2>/dev/null; then
  ok "pipeline markers clean"
else
  die "stale pipeline markers — run: ego-pipeline-sessions.py reconcile-markers ${STATION} --apply"
fi

if [[ "$fail" -eq 0 ]]; then
  echo "==> PASS"
  exit 0
fi
echo "==> FAIL (see above)"
exit 1
