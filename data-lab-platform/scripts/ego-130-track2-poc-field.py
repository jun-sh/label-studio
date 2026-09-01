#!/usr/bin/env python3
"""Deploy Track2 H.264 MCAP code to 130 and run one POC recording (paramiko)."""
from __future__ import annotations

import os
import posixpath
import sys
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SRC = ROOT / "ego-stream-client"
TARGET = os.environ.get("PROVISION_TARGET", "server@10.10.10.130")
USER, HOST = TARGET.split("@", 1)
PASSWORD = os.environ.get("RC_CAPTURE_PASS", "1")

REMOTE_STUDIO = "/home/server/workspace/ego-studio"
REMOTE_CAPTURE = f"{REMOTE_STUDIO}/src/ego_capture_studio/capture"
REMOTE_CLI = f"{REMOTE_STUDIO}/src/ego_capture_studio/cli"
REMOTE_SYSTEMD = "/home/server/.config/systemd/user"
CACHE_ROOT = "/home/server/cache/ego-mcap-track2"


def connect() -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=30)
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 900) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = (o.read() + e.read()).decode(errors="replace")
    code = o.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"remote failed ({code}): {cmd[:120]}\n{out[-4000:]}")
    return out


def upload_tree(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    for path in local.rglob("*"):
        if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = path.relative_to(local)
        remote_path = posixpath.join(remote, rel.as_posix())
        remote_dir = posixpath.dirname(remote_path)
        parts = remote_dir.split("/")
        cur = ""
        for p in parts:
            if not p:
                continue
            cur = f"{cur}/{p}"
            try:
                sftp.stat(cur)
            except OSError:
                sftp.mkdir(cur)
        sftp.put(str(path), remote_path)


def provision(c: paramiko.SSHClient) -> None:
    run(
        c,
        f"mkdir -p {REMOTE_CAPTURE} {REMOTE_CLI} {CACHE_ROOT}/segments /tmp/ego-mcap-track2-active "
        f"{REMOTE_SYSTEMD}/ecs-record-oak-mcap-track2.service.d",
    )
    sftp = c.open_sftp()
    upload_tree(sftp, CAPTURE_SRC, REMOTE_CAPTURE)
    sftp.put(
        str(CAPTURE_SRC / "cli/record_oak_stream.py"),
        f"{REMOTE_CLI}/record_oak_stream.py",
    )
    sftp.put(
        str(CAPTURE_SRC / "systemd/ecs-record-oak-mcap-track2.service"),
        f"{REMOTE_SYSTEMD}/ecs-record-oak-mcap-track2.service",
    )
    sftp.put(
        str(CAPTURE_SRC / "systemd/ecs-record-oak-mcap-track2.service.d/z-mcap-track2-h264.conf"),
        f"{REMOTE_SYSTEMD}/ecs-record-oak-mcap-track2.service.d/z-mcap-track2-h264.conf",
    )
    record_sh = Path(__file__).resolve().parent / "ego-130-record-mcap-track2-poc.sh"
    sftp.put(str(record_sh), "/tmp/ego-130-record-mcap-track2-poc.sh")
    sftp.close()
    run(c, "chmod +x /tmp/ego-130-record-mcap-track2-poc.sh")
    run(c, "systemctl --user daemon-reload")
    print("==> provision OK")


def record(c: paramiko.SSHClient) -> str:
    seconds = os.environ.get("EGO_STRICT_EPISODE_SECONDS", "60")
    print(f"==> starting Track2 POC record (~{seconds}s) …")
    return run(
        c,
        f"EGO_STRICT_EPISODE_SECONDS={seconds} bash /tmp/ego-130-record-mcap-track2-poc.sh",
        timeout=900,
    )


def main() -> None:
    c = connect()
    try:
        provision(c)
        out = record(c)
        print(out)
        for line in out.splitlines():
            if line.startswith("TRACK2_POC_RESULT"):
                print(line)
    finally:
        c.close()


if __name__ == "__main__":
    main()
