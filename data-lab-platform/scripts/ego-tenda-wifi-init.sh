#!/usr/bin/env bash
# Tenda AIC8800DC (2604:0013): eject MSC mode, init driver with OAK USB port gated.
# Run on capture host after dongle is on a dedicated USB port (OAK may stay on its fixed port).
set -euo pipefail

OAK_USB="${EGO_OAK_USB_DEV:-3-4}"
PW="${RC_CAPTURE_PASS:-1}"

sudo_pw() { echo "$PW" | sudo -S "$@"; }

# Tenda on a Genesys hub (3-1.x) may need a branch reset after driver/firmware churn.
if [[ -e /sys/bus/usb/devices/3-1/authorized ]]; then
  sudo_pw sh -c 'echo 0 > /sys/bus/usb/devices/3-1/authorized; sleep 2; echo 1 > /sys/bus/usb/devices/3-1/authorized'
  sleep 3
fi

for node in /dev/aicudisk /dev/sda /dev/sr0; do
  if [[ -e "$node" ]]; then
    sudo_pw eject "$node" 2>/dev/null || true
    sleep 2
  fi
done

if [[ -e "/sys/bus/usb/devices/${OAK_USB}/authorized" ]]; then
  sudo_pw sh -c "echo 0 > /sys/bus/usb/devices/${OAK_USB}/authorized"
  sleep 1
fi

sudo_pw modprobe -r aic8800_fdrv aic_load_fw 2>/dev/null || true
sleep 1
sudo_pw modprobe aic_load_fw
sudo_pw modprobe aic8800_fdrv

iface=""
for _ in $(seq 1 30); do
  iface="$(ip -br link | awk '/^wlx|^wlan[0-9]|^wlp_usb_ap$/ {print $1; exit}')"
  [[ -n "$iface" ]] && break
  sleep 1
done

if [[ -e "/sys/bus/usb/devices/${OAK_USB}/authorized" ]]; then
  sudo_pw sh -c "echo 1 > /sys/bus/usb/devices/${OAK_USB}/authorized"
fi

if [[ -z "$iface" ]]; then
  echo "FAIL: no AIC WiFi netdev (check dongle port / dmesg | grep -i aic)" >&2
  exit 1
fi

echo "OK: AIC WiFi iface=${iface}"
ip -br link show "$iface"
