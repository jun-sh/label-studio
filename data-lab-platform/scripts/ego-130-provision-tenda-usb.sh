#!/usr/bin/env bash
# Install Tenda AIC8800DC init on a GPD capture host (ego-002 class).
# Run AFTER USB dongle is on a fixed port and OAK is on EGO_OAK_USB_DEV (default 3-4).
#
# Usage:
#   EGO_OAK_USB_DEV=3-4 bash ego-130-provision-tenda-usb.sh server@10.10.11.52
set -euo pipefail

TARGET="${1:-server@10.10.11.52}"
OAK_USB="${EGO_OAK_USB_DEV:-3-4}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"

_scp() {
  local src="$1" dst="$2"
  if scp -q -o BatchMode=yes -o ConnectTimeout=8 "$src" "${TARGET}:${dst}" 2>/dev/null; then
    return 0
  fi
  RC_CAPTURE_PASS="${EDGE_PASS}" TARGET="$TARGET" SCP_SRC="$src" SCP_DST="$dst" python3 - <<'PY'
import os, paramiko
target = os.environ["TARGET"]
user, _, host = target.partition("@")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=20)
c.open_sftp().put(os.environ["SCP_SRC"], os.environ["SCP_DST"])
c.close()
PY
}

_run_remote() {
  local script="$1"
  if ssh -o BatchMode=yes -o ConnectTimeout=8 "${TARGET}" "bash -s" <<< "$script"; then
    return 0
  fi
  RC_CAPTURE_PASS="${EDGE_PASS}" TARGET="$TARGET" REMOTE_BODY="$script" python3 - <<'PY'
import os, paramiko
target = os.environ["TARGET"]
user, _, host = target.partition("@")
body = os.environ["REMOTE_BODY"]
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=20)
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{body}\nREMOTE", timeout=300)
print((o.read() + e.read()).decode(), end="")
raise SystemExit(o.channel.recv_exit_status())
PY
}

echo "==> Tenda USB init provision ${TARGET} (OAK_USB=${OAK_USB})"
_scp "${ROOT}/scripts/ego-tenda-wifi-init.sh" "/tmp/ego-tenda-wifi-init.sh"
_scp "${ROOT}/ego-local-web/systemd/ecs-ego-hotspot.service.d/z-tenda-pre.conf" "/tmp/z-tenda-pre.conf"

_run_remote "$(cat <<REMOTE
set -euo pipefail
PW="${EDGE_PASS}"
OAK_USB="${OAK_USB}"

sudo_pw() { echo "\$PW" | sudo -S "\$@"; }

sudo_pw install -m 0755 /tmp/ego-tenda-wifi-init.sh /usr/local/bin/ego-tenda-wifi-init.sh

printf '%s\n' "EGO_OAK_USB_DEV=\${OAK_USB}" > /tmp/ego-tenda.default
sudo_pw install -m 0644 /tmp/ego-tenda.default /etc/default/ego-tenda

sudo_pw mkdir -p /etc/systemd/system/ecs-ego-hotspot.service.d
sudo_pw install -m 0644 /tmp/z-tenda-pre.conf /etc/systemd/system/ecs-ego-hotspot.service.d/z-tenda-pre.conf

sudo_pw systemctl daemon-reload
echo "OK: ego-tenda-wifi-init installed; ecs-ego-hotspot.service.d/z-tenda-pre.conf applied"
REMOTE
)"

echo "==> Done. Optional: sudo systemctl restart ecs-ego-hotspot.service"
