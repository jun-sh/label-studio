#!/usr/bin/env python3
"""Verify 130 ego-standard JPEG production capture path via paramiko."""
from __future__ import annotations

import os
import sys

import paramiko

TARGET = sys.argv[1] if len(sys.argv) > 1 else "server@10.10.10.130"
USER, HOST = TARGET.split("@", 1)
PASSWORD = os.environ.get("RC_CAPTURE_PASS", "1")

REMOTE = r"""
set -euo pipefail
fail=0
die() { echo "FAIL: $*"; fail=1; }
env="$(systemctl --user show ecs-record-oak-stream -p Environment --no-pager 2>/dev/null || true)"
get_env() { echo "$env" | tr ' ' '\n' | grep "^$1=" | cut -d= -f2- || true; }
frame_bin="$(get_env SEGMENT_FRAME_BIN)"
hw_jpeg="$(get_env OAK_HW_JPEG)"
oak_h264="$(get_env OAK_H264)"
[[ "$frame_bin" == "1" ]] || die "SEGMENT_FRAME_BIN=${frame_bin:-unset} (want 1)"
[[ "$hw_jpeg" == "1" ]] || die "OAK_HW_JPEG=${hw_jpeg:-unset} (want 1)"
[[ "$oak_h264" == "0" ]] || die "OAK_H264=${oak_h264:-unset} (want 0)"
grep -q '^OAK_H264=0' "$HOME/.config/ego-station.env.d/station.conf" 2>/dev/null \
  || die "station.conf missing OAK_H264=0"
[[ "$fail" -eq 0 ]] && echo "OK: JPEG-only capture path verified"
exit "$fail"
"""


def main() -> None:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=20)
    _, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{REMOTE}\nREMOTE", timeout=120)
    out = (o.read() + e.read()).decode()
    code = o.channel.recv_exit_status()
    print(out.strip())
    if code != 0:
        sys.exit(code)


if __name__ == "__main__":
    main()
