#!/usr/bin/env python3
"""Provision ROS station autostart on 10.10.10.200 via SSH (paramiko)."""
from __future__ import annotations

import os
import posixpath
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent
TARGET = os.environ.get("PROVISION_TARGET", "sri@10.10.10.200")
PASSWORD = os.environ.get("RC_CAPTURE_PASS", "1")
SSH_PORT = int(os.environ.get("PROVISION_SSH_PORT", "22"))
REBOOT_HOUR = os.environ.get("REBOOT_HOUR", "4")
REBOOT_MINUTE = os.environ.get("REBOOT_MINUTE", "0")
ROS_STATION_USER = os.environ.get("ROS_STATION_USER", "server")

USER, HOST = TARGET.split("@", 1)
REMOTE_DIR = "/tmp/ros-station-provision"


def connect() -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=SSH_PORT, username=USER, password=PASSWORD, timeout=20)
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 300) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = (o.read() + e.read()).decode()
    code = o.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"remote failed ({code}): {cmd[:120]}\n{out}")
    return out


def upload_dir(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    for path in local.iterdir():
        remote_path = posixpath.join(remote, path.name)
        if path.is_dir():
            try:
                sftp.stat(remote_path)
            except OSError:
                sftp.mkdir(remote_path)
            upload_dir(sftp, path, remote_path)
        else:
            sftp.put(str(path), remote_path)


def main() -> None:
    print(f"Connecting to {TARGET}:{SSH_PORT} ...")
    c = connect()
    run(c, f"mkdir -p {REMOTE_DIR}")
    sftp = c.open_sftp()
    for name in ("ros-station-start.sh", "ros-station.service", "install-ros-station-autostart.sh"):
        sftp.put(str(ROOT / name), posixpath.join(REMOTE_DIR, name))
    sftp.close()

    install_cmd = (
        f"cd {REMOTE_DIR} && "
        f"chmod +x install-ros-station-autostart.sh ros-station-start.sh && "
        f"echo '{PASSWORD}' | sudo -S env "
        f"REBOOT_HOUR={REBOOT_HOUR} REBOOT_MINUTE={REBOOT_MINUTE} "
        f"ROS_STATION_USER={ROS_STATION_USER} "
        f"bash install-ros-station-autostart.sh"
    )
    out = run(c, install_cmd, timeout=120)
    print(out)
    print(f"Done — ROS station autostart provisioned on {HOST}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "\nIf SSH port 22 is refused, enable it on the target host first:\n"
            "  sudo apt install -y openssh-server\n"
            "  sudo systemctl enable --now ssh\n"
            "\nOr copy data-lab-platform/ros-station/ to the host and run:\n"
            "  sudo bash install-ros-station-autostart.sh\n",
            file=sys.stderr,
        )
        sys.exit(1)
