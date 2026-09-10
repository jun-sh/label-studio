#!/usr/bin/env bash
# Dual-WiFi production layout on 130:
#   USB (wlp_usb_ap)  -> EGO-001-COLLECT hotspot AP
#   AX201 (wlo1)      -> SRI-EI lab WiFi client (upstream internet)
#
# Usage:
#   ego-130-provision-usb-hotspot.sh [ssh_target] [usb_iface]
set -euo pipefail

TARGET="${1:-server@10.10.10.130}"
USB_IFACE="${2:-wlp_usb_ap}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EGO_WEB="${ROOT}/ego-local-web"

CONN="${EGO_HOTSPOT_CONN:-EGO-001-COLLECT}"
GATEWAY="${EGO_HOTSPOT_GATEWAY:-192.168.8.1}"
USB_MAC="${EGO_USB_WIFI_MAC:-50:2b:73:e8:46:0c}"
WLO1_CONN="${EGO_WLO1_WIFI_CONN:-SRI-WIFI}"
BOOT_DELAY="${EGO_HOTSPOT_BOOT_DELAY_SEC:-45}"
EDGE_PASS="${RC_CAPTURE_PASS:-1}"

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
_, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{body}\nREMOTE", timeout=240)
print((o.read() + e.read()).decode(), end="")
raise SystemExit(o.channel.recv_exit_status())
PY
}

for f in ego-wifi-radio-on.sh ego-wlo1-wifi-up.sh ego-hotspot-up.sh ego-hotspot-down.sh \
  ego-hotspot-boot-wait.sh ego-hotspot-watchdog.sh; do
  _scp "${EGO_WEB}/scripts/${f}" "/tmp/${f}"
done
for f in ecs-ego-wifi-radio.service ecs-ego-wlo1-wifi.service ecs-ego-hotspot.service \
  ecs-ego-hotspot-watchdog.service ecs-ego-hotspot-watchdog.timer; do
  _scp "${EGO_WEB}/systemd/${f}" "/tmp/${f}"
done
_scp "${EGO_WEB}/udev/70-ego-usb-wifi.rules" "/tmp/70-ego-usb-wifi.rules"

_run_remote "$(cat <<REMOTE
set -euo pipefail
PW="${EDGE_PASS}"
USB_IFACE="${USB_IFACE}"
USB_MAC="${USB_MAC}"
CONN="${CONN}"
GATEWAY="${GATEWAY}"
WLO1_CONN="${WLO1_CONN}"
BOOT_DELAY="${BOOT_DELAY}"

sudo_pw() { echo "\$PW" | sudo -S "\$@"; }

install -d -m 0755 /home/server/ego-web
for f in ego-wifi-radio-on.sh ego-wlo1-wifi-up.sh ego-hotspot-up.sh ego-hotspot-down.sh \
  ego-hotspot-boot-wait.sh ego-hotspot-watchdog.sh; do
  install -m 0755 "/tmp/\$f" "/home/server/ego-web/\$f"
done
chown server:server /home/server/ego-web/ego-*.sh /home/server/ego-web/ego-hotspot-*.sh 2>/dev/null || true

sudo_pw install -m 0644 /tmp/70-ego-usb-wifi.rules /etc/udev/rules.d/70-ego-usb-wifi.rules
sudo_pw udevadm control --reload-rules
sudo_pw udevadm trigger --subsystem-match=net --action=add || true

printf '%s\n' \
  "EGO_WIFI_IFACE=\${USB_IFACE}" \
  "EGO_USB_WIFI_MAC=\${USB_MAC}" \
  "EGO_HOTSPOT_CONN=\${CONN}" \
  "EGO_HOTSPOT_GATEWAY=\${GATEWAY}" \
  "EGO_WLO1_IFACE=wlo1" \
  "EGO_WLO1_WIFI_CONN=\${WLO1_CONN}" \
  "EGO_HOTSPOT_BOOT_DELAY_SEC=\${BOOT_DELAY}" \
  "EGO_HOTSPOT_READY_TIMEOUT_SEC=120" \
  "EGO_HOTSPOT_RELOAD_COOLDOWN_SEC=300" \
  > /tmp/ego-hotspot.default
sudo_pw install -m 0644 /tmp/ego-hotspot.default /etc/default/ego-hotspot
rm -f /tmp/ego-hotspot.default

for f in ecs-ego-wifi-radio.service ecs-ego-wlo1-wifi.service ecs-ego-hotspot.service \
  ecs-ego-hotspot-watchdog.service ecs-ego-hotspot-watchdog.timer; do
  sudo_pw install -m 0644 "/tmp/\$f" "/etc/systemd/system/\$f"
done
sudo_pw systemctl daemon-reload

# Resolve current USB iface (before udev rename takes effect on next boot).
if ! ip link show "\$USB_IFACE" &>/dev/null; then
  cand="\$(nmcli -t -f DEVICE,TYPE device status | awk -F: '\$2==\"wifi\" && \$1 ~ /^wlx/ {print \$1; exit}')"
  [[ -n "\$cand" ]] && USB_IFACE="\$cand"
fi
echo "USB AP iface: \$USB_IFACE"

if ! nmcli connection show "\$CONN" &>/dev/null; then
  echo "ERROR: NM connection \$CONN not found" >&2
  exit 1
fi

sudo_pw /home/server/ego-web/ego-wifi-radio-on.sh

sudo_pw nmcli connection modify "\$CONN" connection.interface-name "\$USB_IFACE"
sudo_pw nmcli connection modify "\$CONN" connection.autoconnect yes
sudo_pw nmcli connection modify "\$CONN" connection.autoconnect-priority 100
sudo_pw nmcli connection modify "\$CONN" 802-11-wireless.band bg
sudo_pw nmcli connection modify "\$CONN" 802-11-wireless.channel 6

if nmcli connection show "\$WLO1_CONN" &>/dev/null; then
  sudo_pw nmcli connection modify "\$WLO1_CONN" connection.interface-name wlo1
  sudo_pw nmcli connection modify "\$WLO1_CONN" connection.autoconnect yes
  sudo_pw nmcli connection modify "\$WLO1_CONN" connection.autoconnect-priority 50
  echo "Lab WiFi profile: \$WLO1_CONN on wlo1 (autoconnect)"
fi

sudo_pw systemctl enable ecs-ego-wifi-radio.service
sudo_pw systemctl enable ecs-ego-wlo1-wifi.service
sudo_pw systemctl enable ecs-ego-hotspot.service
sudo_pw systemctl enable ecs-ego-hotspot-watchdog.timer

sudo_pw systemctl restart ecs-ego-wifi-radio.service
sleep 2
sudo_pw systemctl restart ecs-ego-wlo1-wifi.service || true
sleep 2
sudo_pw systemctl restart ecs-ego-hotspot.service
sudo_pw systemctl restart ecs-ego-hotspot-watchdog.timer

sleep 3
echo "=== verify ==="
rfkill list | head -6 || true
nmcli dev status | grep -E 'wlo1|wlp_usb|wlx502' || true
nmcli connection show --active
iwconfig "\$USB_IFACE" 2>/dev/null | grep -E 'Mode|ESSID' || iwconfig wlx502b73e8460c 2>/dev/null | grep -E 'Mode|ESSID' || true
systemctl is-active ecs-ego-wifi-radio.service ecs-ego-wlo1-wifi.service ecs-ego-hotspot.service ecs-ego-hotspot-watchdog.timer
echo "OK: dual WiFi layout provisioned"
REMOTE
)"

echo "Done. Reboot 130 once to validate cold-boot: USB AP + wlo1 lab WiFi."
