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
for d in "$HOME/.config/systemd/user/ecs-record-oak-stream.service.d" \
           /etc/systemd/system/ecs-record-oak-stream.service.d; do
  [[ -d "$d" ]] || continue
  [[ -f "$d/v0.0.8-segment-mp4.conf" ]] || die "$d/v0.0.8-segment-mp4.conf missing"
done
env="$(systemctl --user show ecs-record-oak-stream -p Environment --no-pager 2>/dev/null || true)"
get_env() { echo "$env" | tr ' ' '\n' | grep "^$1=" | cut -d= -f2- || true; }
seg_h264="$(get_env SEGMENT_H264)"
frame_bin="$(get_env SEGMENT_FRAME_BIN)"
hw_jpeg="$(get_env OAK_HW_JPEG)"
preview_edge="$(get_env PREVIEW_MAX_EDGE)"
imu_interp="$(get_env EGO_IMU_INTERPOLATE)"
[[ "$seg_h264" == "0" ]] || die "SEGMENT_H264=${seg_h264:-unset} (want 0)"
[[ "$frame_bin" == "1" ]] || die "SEGMENT_FRAME_BIN=${frame_bin:-unset} (want 1)"
[[ "$hw_jpeg" == "1" ]] || die "OAK_HW_JPEG=${hw_jpeg:-unset} (want 1)"
[[ "$preview_edge" == "1280" ]] || die "PREVIEW_MAX_EDGE=${preview_edge:-unset} (want 1280)"
[[ "$imu_interp" == "1" ]] || die "EGO_IMU_INTERPOLATE=${imu_interp:-unset} (want 1)"
[[ "$fail" -eq 0 ]] && echo "OK: ego-standard JPEG production path verified"
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
