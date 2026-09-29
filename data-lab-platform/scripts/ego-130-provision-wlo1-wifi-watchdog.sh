#!/usr/bin/env bash
# Install/update EGO lab WiFi (wlo1 / SRI-WIFI) reconnect watchdog on capture host (130).
# Usage: ego-130-provision-wlo1-wifi-watchdog.sh [ssh_target]
# Example: ego-130-provision-wlo1-wifi-watchdog.sh server@10.10.10.130
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EGO_WEB="${ROOT}/ego-local-web"

WLO1_IFACE="${EGO_WLO1_IFACE:-wlo1}"
WLO1_CONN="${EGO_WLO1_WIFI_CONN:-SRI-WIFI}"
HOTSPOT_IFACE="${EGO_WIFI_IFACE:-wlp_usb_ap}"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"

echo "==> Provision lab WiFi watchdog ${TARGET} (${WLO1_CONN} on ${WLO1_IFACE})"

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

for f in ego-wifi-radio-on.sh ego-wlo1-wifi-up.sh ego-wlo1-wifi-watchdog.sh; do
  _scp "${EGO_WEB}/scripts/${f}" "/tmp/${f}"
done
for f in ecs-ego-wlo1-wifi-watchdog.service ecs-ego-wlo1-wifi-watchdog.timer; do
  _scp "${EGO_WEB}/systemd/${f}" "/tmp/${f}"
done

_run_remote "$(cat <<REMOTE
set -euo pipefail
PW="${EDGE_PASS}"
sudo_pw() { echo "\$PW" | sudo -S "\$@"; }

install -d -m 0755 /home/server/ego-web
for f in ego-wifi-radio-on.sh ego-wlo1-wifi-up.sh ego-wlo1-wifi-watchdog.sh; do
  install -m 0755 "/tmp/\$f" "/home/server/ego-web/\$f"
done
chown server:server /home/server/ego-web/ego-wifi-radio-on.sh /home/server/ego-web/ego-wlo1-wifi-*.sh 2>/dev/null || true

if [[ -f /etc/default/ego-hotspot ]]; then
  grep -q '^EGO_WLO1_IFACE=' /etc/default/ego-hotspot || echo "EGO_WLO1_IFACE=${WLO1_IFACE}" | sudo_pw tee -a /etc/default/ego-hotspot >/dev/null
  grep -q '^EGO_WLO1_WIFI_CONN=' /etc/default/ego-hotspot || echo "EGO_WLO1_WIFI_CONN=${WLO1_CONN}" | sudo_pw tee -a /etc/default/ego-hotspot >/dev/null
  grep -q '^EGO_WIFI_IFACE=' /etc/default/ego-hotspot || echo "EGO_WIFI_IFACE=${HOTSPOT_IFACE}" | sudo_pw tee -a /etc/default/ego-hotspot >/dev/null
fi

for f in ecs-ego-wlo1-wifi-watchdog.service ecs-ego-wlo1-wifi-watchdog.timer; do
  sudo_pw install -m 0644 "/tmp/\$f" "/etc/systemd/system/\$f"
done

sudo_pw systemctl daemon-reload
sudo_pw systemctl enable ecs-ego-wlo1-wifi-watchdog.timer
sudo_pw systemctl restart ecs-ego-wlo1-wifi-watchdog.timer

echo "OK: lab WiFi watchdog installed"
systemctl is-active ecs-ego-wlo1-wifi-watchdog.timer || true
systemctl list-timers ecs-ego-wlo1-wifi-watchdog.timer --no-pager | sed -n '1,3p'
REMOTE
)"

echo "Done. Verify on 130:"
echo "  systemctl status ecs-ego-wlo1-wifi-watchdog.timer"
echo "  journalctl -t ego-wlo1-wifi-watchdog -n 20"
