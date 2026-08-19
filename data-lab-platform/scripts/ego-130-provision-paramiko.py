#!/usr/bin/env python3
"""Provision 130 via paramiko — 8857c7a JPEG/staging baseline + retained 4 RGB topology."""
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
REMOTE_CONFIG = f"{REMOTE_STUDIO}/config"
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
        f"mkdir -p {REMOTE_CAPTURE} {REMOTE_CLI} {REMOTE_CONFIG} {CACHE_ROOT}/segments {CACHE_ROOT}/logs",
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
    for topo in ("camera_topology_standard.json", "camera_topology_standard.yaml"):
        local_topo = CAPTURE_SRC / "config" / topo
        if local_topo.is_file():
            sftp.put(str(local_topo), f"{REMOTE_CONFIG}/{topo}")
    prod_conf = CAPTURE_SRC / "systemd/ecs-record-oak-stream.service.d/z-production-egoverse.conf"
    sftp.put(str(prod_conf), "/tmp/z-production-egoverse.conf")
    hb_svc = CAPTURE_SRC / "systemd/ecs-station-heartbeat.service"
    if hb_svc.is_file():
        sftp.put(str(hb_svc), "/tmp/ecs-station-heartbeat.service")
    sftp.close()

    remote_script = f"""
set -euo pipefail
echo '1' | sudo -S apt-get install -y ffmpeg rsync 2>/dev/null || sudo apt-get install -y ffmpeg rsync
for d in /etc/systemd/system/ecs-record-oak-stream.service.d \\
           "$HOME/.config/systemd/user/ecs-record-oak-stream.service.d"; do
  echo '1' | sudo -S mkdir -p "$d" 2>/dev/null || mkdir -p "$d"
  echo '1' | sudo -S cp /tmp/z-production-egoverse.conf "$d/z-production-egoverse.conf" 2>/dev/null \\
    || cp /tmp/z-production-egoverse.conf "$d/z-production-egoverse.conf"
  for bad in v0.0.8-segment-mp4.conf ego-standard-production.conf scheme-a.conf \\
               v0.0.8-segment-mp4-genpts.conf v0.0.9-h264-plan-b.conf phase2-h264-poc.conf; do
    if [[ -f "$d/$bad" ]]; then
      echo '1' | sudo -S mv "$d/$bad" "$d/$bad.disabled" 2>/dev/null || mv "$d/$bad" "$d/$bad.disabled"
      echo "disabled $d/$bad"
    fi
  done
done
mkdir -p "$HOME/.config/ego-station.env.d"
cat > "$HOME/.config/ego-station.env" <<EOF
EGO_STATION_ID={STATION_ID}
EGO_SEGMENT_ROOT={CACHE_ROOT}/segments
EGO_EXPORT_ROOT=/home/server/export/{STATION_ID}
EGO_CAPTURE_CHECKPOINT={CACHE_ROOT}/segments/checkpoint.json
EGO_UPLOAD_LOG_DIR={CACHE_ROOT}/logs
DATALAB_HEARTBEAT_URL=http://10.10.10.34:8080/lerobot/api/collection/stations/{STATION_ID}/upload
EGO_UPLOAD_URL=http://10.10.10.34:8080/lerobot/api/collection/stations/{STATION_ID}/upload
STATION_UPLOAD_TOKEN=dl-upload-{STATION_ID}-v1
DATALAB_CAPTURE_HOST={HOST}
EGO_SEGMENT_DELETE_AFTER_UPLOAD=1
UPLOAD_PROTOCOL=tarzst
EOF
cat > "$HOME/.config/ego-station.env.d/station.conf" <<EOF
EGO_SEGMENT_ROOT={CACHE_ROOT}/segments
EGO_CAPTURE_CHECKPOINT={CACHE_ROOT}/segments/checkpoint.json
EGO_UPLOAD_URL=http://10.10.10.34:8080/lerobot/api/collection/stations/{STATION_ID}/upload
DATALAB_HEARTBEAT_URL=http://10.10.10.34:8080/lerobot/api/collection/stations/{STATION_ID}/upload
STATION_UPLOAD_TOKEN=dl-upload-{STATION_ID}-v1
DATALAB_CAPTURE_HOST={HOST}
EGO_TOPOLOGY_FILE={REMOTE_CONFIG}/camera_topology_standard.yaml
SEGMENT_FRAME_BIN=1
OAK_HW_JPEG=1
OAK_H264=0
UPLOAD_PROTOCOL=tarzst
EOF
printf 'EGO_UPLOAD_MODE=production\\n' > "$HOME/.config/ego-station.env.d/upload-mode.conf"
mkdir -p "$HOME/.config/systemd/user"
if [[ -f /tmp/ecs-station-heartbeat.service ]]; then
  cp /tmp/ecs-station-heartbeat.service "$HOME/.config/systemd/user/ecs-station-heartbeat.service"
fi
LOOP_UNIT=ecs-upload-segments-loop.service
systemctl --user stop "$LOOP_UNIT" 2>/dev/null || true
systemctl --user disable "$LOOP_UNIT" 2>/dev/null || true
systemctl --user mask "$LOOP_UNIT" 2>/dev/null || true
pkill -f '[u]pload_segments_loop.py' 2>/dev/null || true
systemctl --user daemon-reload 2>/dev/null || true
echo '1' | sudo -S systemctl daemon-reload 2>/dev/null || true
systemctl --user enable ecs-station-heartbeat.service 2>/dev/null || true
systemctl --user start ecs-station-heartbeat.service 2>/dev/null || true
"""
    run_script(c, remote_script)

    verify = run(
        c,
        "systemctl --user show ecs-record-oak-stream.service -p Environment --value | tr ' ' '\\n' | "
        "grep -E '^(SEGMENT_FRAME_BIN|OAK_HW_JPEG|OAK_H264)=' | sort",
    ).strip()
    print("capture env:", verify.replace("\n", " "))
    print(run(c, "which ffmpeg && ffmpeg -version | head -1").strip())
    c.close()
    print(f"provision ok: {TARGET} station={STATION_ID}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
