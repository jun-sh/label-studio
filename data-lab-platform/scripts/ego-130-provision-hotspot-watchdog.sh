#!/usr/bin/env bash
# Install/update EGO hotspot watchdog on capture host (130).
# Usage: ego-130-provision-hotspot-watchdog.sh [ssh_target]
# Example: ego-130-provision-hotspot-watchdog.sh server@10.10.10.130
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EGO_WEB="${ROOT}/ego-local-web"

IFACE="${EGO_WIFI_IFACE:-wlo1}"
CONN="${EGO_HOTSPOT_CONN:-EGO-001-COLLECT}"
GATEWAY="${EGO_HOTSPOT_GATEWAY:-192.168.8.1}"
BOOT_DELAY="${EGO_HOTSPOT_BOOT_DELAY_SEC:-720}"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"

echo "==> Provision hotspot watchdog ${TARGET} (${CONN} on ${IFACE})"

_scp() {
  local src="$1" dst="$2"
  if scp -q -o BatchMode=yes -o ConnectTimeout=8 "$src" "${TARGET}:${dst}" 2>/dev/null; then
    return 0
  fi
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" TARGET="$TARGET" SCP_SRC="$src" SCP_DST="$dst" python3 - <<'PY'
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
  RC_CAPTURE_PASS="${RC_CAPTURE_PASS:-1}" TARGET="$TARGET" REMOTE_BODY="$script" python3 - <<'PY'
import os, paramiko
target = os.environ["TARGET"]
user, _, host = target.partition("@")
body = os.environ["REMOTE_BODY"]
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=user, password=os.environ.get("RC_CAPTURE_PASS", "1"), timeout=20)
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{body}\nREMOTE", timeout=180)
print((o.read() + e.read()).decode(), end="")
raise SystemExit(o.channel.recv_exit_status())
PY
}

for f in ego-hotspot-up.sh ego-hotspot-down.sh ego-hotspot-boot-wait.sh ego-hotspot-watchdog.sh; do
  _scp "${EGO_WEB}/scripts/${f}" "/tmp/${f}"
done
for f in ecs-ego-hotspot.service ecs-ego-hotspot-watchdog.service ecs-ego-hotspot-watchdog.timer; do
  _scp "${EGO_WEB}/systemd/${f}" "/tmp/${f}"
done

_run_remote "$(cat <<REMOTE
set -euo pipefail
PW="${EDGE_PASS}"
sudo_pw() { echo "\$PW" | sudo -S "\$@"; }

install -d -m 0755 /home/server/ego-web
for f in ego-hotspot-up.sh ego-hotspot-down.sh ego-hotspot-boot-wait.sh ego-hotspot-watchdog.sh; do
  install -m 0755 "/tmp/\$f" "/home/server/ego-web/\$f"
done
chown server:server /home/server/ego-web/ego-hotspot-*.sh

printf '%s\n' \
  "EGO_WIFI_IFACE=${IFACE}" \
  "EGO_HOTSPOT_CONN=${CONN}" \
  "EGO_HOTSPOT_GATEWAY=${GATEWAY}" \
  "EGO_HOTSPOT_BOOT_DELAY_SEC=${BOOT_DELAY}" \
  "EGO_HOTSPOT_RELOAD_COOLDOWN_SEC=300" \
  > /tmp/ego-hotspot.default
sudo_pw install -m 0644 /tmp/ego-hotspot.default /etc/default/ego-hotspot
rm -f /tmp/ego-hotspot.default

for f in ecs-ego-hotspot.service ecs-ego-hotspot-watchdog.service ecs-ego-hotspot-watchdog.timer; do
  sudo_pw install -m 0644 "/tmp/\$f" "/etc/systemd/system/\$f"
done

sudo_pw systemctl daemon-reload
sudo_pw systemctl enable ecs-ego-hotspot.service
sudo_pw systemctl enable ecs-ego-hotspot-watchdog.timer
sudo_pw systemctl restart ecs-ego-hotspot-watchdog.timer
sudo_pw systemctl restart ecs-ego-hotspot.service

echo "OK: hotspot watchdog installed"
systemctl is-active ecs-ego-hotspot.service || true
systemctl is-active ecs-ego-hotspot-watchdog.timer || true
systemctl list-timers ecs-ego-hotspot-watchdog.timer --no-pager | sed -n '1,3p'
REMOTE
)"

echo "Done. Verify on 130:"
echo "  systemctl status ecs-ego-hotspot-watchdog.timer"
echo "  journalctl -t ego-hotspot-watchdog -n 20"
