#!/usr/bin/env python3
"""Verify 130 H264 production path via paramiko."""
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
for d in "$HOME/.config/systemd/user/ecs-record-oak-stream.service.d" \
           /etc/systemd/system/ecs-record-oak-stream.service.d; do
  [[ -d "$d" ]] || continue
  for bad in z-production-egoverse.conf scheme-a.conf; do
    [[ -f "$d/$bad" ]] && die "$d/$bad present"
  done
  [[ -f "$d/v0.0.8-segment-mp4.conf" ]] || die "$d/v0.0.8-segment-mp4.conf missing"
done
env="$(systemctl --user show ecs-record-oak-stream -p Environment --no-pager 2>/dev/null || true)"
seg_h264="$(echo "$env" | tr ' ' '\n' | grep '^SEGMENT_H264=' | cut -d= -f2 || true)"
legacy_append="$(echo "$env" | tr ' ' '\n' | grep '^SEGMENT_H264_LEGACY_APPEND=' | cut -d= -f2 || true)"
frame_bin="$(echo "$env" | tr ' ' '\n' | grep '^SEGMENT_FRAME_BIN=' | cut -d= -f2 || true)"
[[ "$seg_h264" == "1" ]] || die "SEGMENT_H264=${seg_h264:-unset}"
[[ "$legacy_append" == "1" ]] || die "SEGMENT_H264_LEGACY_APPEND=${legacy_append:-unset}"
[[ "$frame_bin" == "0" ]] || die "SEGMENT_FRAME_BIN=${frame_bin:-unset}"
command -v ffmpeg >/dev/null || die "ffmpeg missing"
[[ "$fail" -eq 0 ]] && echo "OK: segment H264 production path verified"
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
