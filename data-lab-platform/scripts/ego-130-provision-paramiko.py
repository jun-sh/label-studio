#!/usr/bin/env python3
"""Provision 130 via paramiko (password auth). Mirrors ego-130-provision.sh."""
from __future__ import annotations

import os
import posixpath
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SRC = ROOT / "ego-stream-client"
TARGET = os.environ.get("PROVISION_TARGET", "server@10.10.10.130")
STATION_ID = os.environ.get("PROVISION_STATION", "ego-001")
USER, HOST = TARGET.split("@", 1)
PASSWORD = os.environ.get("RC_CAPTURE_PASS", "1")

REMOTE_STUDIO = "/home/server/workspace/ego-studio"
REMOTE_CAPTURE = f"{REMOTE_STUDIO}/src/ego_capture_studio/capture"
REMOTE_CLI = f"{REMOTE_STUDIO}/src/ego_capture_studio/cli"
CACHE_ROOT = f"/home/server/cache/{STATION_ID}"


def connect() -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=20)
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 600) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = (o.read() + e.read()).decode()
    code = o.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"remote failed ({code}): {cmd[:80]}\n{out}")
    return out


def run_script(c: paramiko.SSHClient, script: str, timeout: int = 600) -> str:
    _, o, e = c.exec_command("bash -s", timeout=timeout)
    o.channel.send(script)
    o.channel.shutdown_write()
    out = (o.read() + e.read()).decode()
    code = o.channel.recv_exit_status()
    if code != 0:
        raise RuntimeError(f"remote script failed ({code})\n{out}")
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


def main() -> None:
    c = connect()
    run(
        c,
        f"mkdir -p {REMOTE_CAPTURE} {REMOTE_CLI} {CACHE_ROOT}/segments {CACHE_ROOT}/logs",
    )
    sftp = c.open_sftp()
    upload_tree(sftp, CAPTURE_SRC, REMOTE_CAPTURE)
    for name in (
        "camera_map.py",
        "topology.py",
        "segment_tar_zst.py",
        "segment_upload.py",
        "upload_status.py",
    ):
        local = CAPTURE_SRC / name
        if local.is_file():
            sftp.put(str(local), f"{REMOTE_CAPTURE}/{name}")
    sftp.put(
        str(CAPTURE_SRC / "cli/record_oak_stream.py"),
        f"{REMOTE_CLI}/record_oak_stream.py",
    )
    sftp.put(
        str(CAPTURE_SRC / "cli/upload_segments.py"),
        f"{REMOTE_CLI}/upload_segments.py",
    )
    tools_remote = f"{REMOTE_STUDIO}/src/ego_capture_studio/tools"
    run(c, f"mkdir -p {tools_remote}")
    sftp.put(
        str(CAPTURE_SRC / "tools/upload_segments_loop.py"),
        f"{tools_remote}/upload_segments_loop.py",
    )
    conf_local = CAPTURE_SRC / "systemd/ecs-record-oak-stream.service.d/v0.0.8-segment-mp4.conf"
    sftp.put(str(conf_local), "/tmp/v0.0.8-segment-mp4.conf")
    prod_conf = CAPTURE_SRC / "systemd/ecs-record-oak-stream.service.d/ego-standard-production.conf"
    if prod_conf.is_file():
        sftp.put(str(prod_conf), "/tmp/ego-standard-production.conf")
    prod_dropin = CAPTURE_SRC / "systemd/ecs-upload-segments-loop.service.d/production-manual-only.conf"
    sftp.put(str(prod_dropin), "/tmp/production-manual-only.conf")
    sftp.close()

    remote_script = f"""
set -euo pipefail
echo '1' | sudo -S apt-get install -y ffmpeg rsync 2>/dev/null || sudo apt-get install -y ffmpeg rsync
for d in /etc/systemd/system/ecs-record-oak-stream.service.d \\
           "$HOME/.config/systemd/user/ecs-record-oak-stream.service.d"; do
  echo '1' | sudo -S mkdir -p "$d" 2>/dev/null || mkdir -p "$d"
  echo '1' | sudo -S cp /tmp/v0.0.8-segment-mp4.conf "$d/v0.0.8-segment-mp4.conf" 2>/dev/null \\
    || cp /tmp/v0.0.8-segment-mp4.conf "$d/v0.0.8-segment-mp4.conf"
  if [[ -f /tmp/ego-standard-production.conf ]]; then
    echo '1' | sudo -S cp /tmp/ego-standard-production.conf "$d/ego-standard-production.conf" 2>/dev/null \\
      || cp /tmp/ego-standard-production.conf "$d/ego-standard-production.conf"
  fi
  for bad in z-production-egoverse.conf scheme-a.conf v0.0.9-h264-plan-b.conf; do
  if [[ -f "$d/$bad" ]]; then
    echo '1' | sudo -S mv "$d/$bad" "$d/$bad.disabled" 2>/dev/null || mv "$d/$bad" "$d/$bad.disabled"
  fi
  done
done
mkdir -p "$HOME/.config/ego-station.env.d"
if [[ -f "$HOME/.config/ego-station.env" ]]; then
  grep -q '^SEGMENT_H264_LEGACY_APPEND=' "$HOME/.config/ego-station.env" \\
    && sed -i 's/^SEGMENT_H264_LEGACY_APPEND=.*/SEGMENT_H264_LEGACY_APPEND=0/' "$HOME/.config/ego-station.env" \\
    || echo 'SEGMENT_H264_LEGACY_APPEND=0' >> "$HOME/.config/ego-station.env"
  grep -q '^SEGMENT_FRAME_BIN=' "$HOME/.config/ego-station.env" \\
    && sed -i 's/^SEGMENT_FRAME_BIN=.*/SEGMENT_FRAME_BIN=1/' "$HOME/.config/ego-station.env" \\
    || echo 'SEGMENT_FRAME_BIN=1' >> "$HOME/.config/ego-station.env"
  grep -q '^SEGMENT_H264=' "$HOME/.config/ego-station.env" \\
    && sed -i 's/^SEGMENT_H264=.*/SEGMENT_H264=0/' "$HOME/.config/ego-station.env" \\
    || echo 'SEGMENT_H264=0' >> "$HOME/.config/ego-station.env"
  sed -i '/^Segment_H264_LEGACY_APPEND/d' "$HOME/.config/ego-station.env" 2>/dev/null || true
fi
cat > "$HOME/.config/ego-station.env.d/station.conf" <<EOF
EGO_SEGMENT_ROOT={CACHE_ROOT}/segments
EGO_CAPTURE_CHECKPOINT={CACHE_ROOT}/segments/checkpoint.json
EGO_UPLOAD_URL=http://10.10.10.34:8080/lerobot/api/collection/stations/{STATION_ID}/upload
EGO_TOPOLOGY_FILE={REMOTE_CAPTURE}/config/camera_topology_standard.json
SEGMENT_H264=0
SEGMENT_H264_LEGACY_APPEND=0
SEGMENT_FRAME_BIN=1
OAK_HW_JPEG=1
OAK_H264=0
OAK_HW_PREVIEW=1
OAK_HW_PREVIEW_H264=0
EGO_FRAME_INTERVAL_MS=33
EGO_IMU_INTERPOLATE=1
OAK_GPIO_FSYNC=1
PREVIEW_MAX_EDGE=1280
PREVIEW_FPS=15
EGO_SEGMENT_PERSIST_QUEUE_MAX=2048
UPLOAD_PROTOCOL=tarzst
EOF
systemctl --user daemon-reload 2>/dev/null || true
echo '1' | sudo -S systemctl daemon-reload 2>/dev/null || true
LOOP_UNIT=ecs-upload-segments-loop.service
DROPIN_DIR="$HOME/.config/systemd/user/ecs-upload-segments-loop.service.d"
systemctl --user stop "$LOOP_UNIT" 2>/dev/null || true
systemctl --user disable "$LOOP_UNIT" 2>/dev/null || true
systemctl --user mask "$LOOP_UNIT" 2>/dev/null || true
pkill -f '[u]pload_segments_loop.py' 2>/dev/null || true
mkdir -p "$DROPIN_DIR"
rm -f "$DROPIN_DIR/production-manual-only.conf" "$DROPIN_DIR/debug-auto-upload.conf"
cp /tmp/production-manual-only.conf "$DROPIN_DIR/production-manual-only.conf"
mkdir -p "$HOME/.config/ego-station.env.d"
printf 'EGO_UPLOAD_MODE=production\n' > "$HOME/.config/ego-station.env.d/upload-mode.conf"
systemctl --user daemon-reload 2>/dev/null || true
"""
    run_script(c, remote_script)

    print(run(c, "which ffmpeg && ffmpeg -version | head -1").strip())
    c.close()
    print(f"provision ok: {TARGET} station={STATION_ID}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
