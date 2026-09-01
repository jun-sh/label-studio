#!/usr/bin/env python3
"""Track2 E2E: 130 record -> ego-upload -> local ego-process -> verify."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "data-lab-platform" / "scripts"
STATION = "ego-mcap-track2"
TARGET = os.environ.get("PROVISION_TARGET", "server@10.10.10.130")
USER, HOST = TARGET.split("@", 1)
PASSWORD = os.environ.get("RC_CAPTURE_PASS", "1")
RECORD_SECONDS = os.environ.get("EGO_STRICT_EPISODE_SECONDS", "60")
SKIP_RECORD = os.environ.get("SKIP_RECORD", "0") == "1"


def connect() -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=30)
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 1200) -> tuple[int, str]:
    print(f"\n[130] $ {cmd[:200]}")
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = (o.read() + e.read()).decode(errors="replace")
    code = o.channel.recv_exit_status()
    print(out[-8000:] if len(out) > 8000 else out)
    return code, out


def provision_upload_script(c: paramiko.SSHClient) -> None:
    sftp = c.open_sftp()
    sftp.put(str(SCRIPTS / "ego-upload-station.sh"), "/tmp/ego-upload-station.sh")
    sftp.close()
    run(
        c,
        "mkdir -p ~/.local/bin && cp /tmp/ego-upload-station.sh ~/.local/bin/ego-upload && chmod +x ~/.local/bin/ego-upload",
    )


def record_on_130(c: paramiko.SSHClient) -> str:
    sftp = c.open_sftp()
    sftp.put(str(SCRIPTS / "ego-130-record-mcap-track2-poc.sh"), "/tmp/ego-130-record-mcap-track2-poc.sh")
    sftp.close()
    run(c, "chmod +x /tmp/ego-130-record-mcap-track2-poc.sh")
    code, out = run(
        c,
        f"EGO_STRICT_EPISODE_SECONDS={RECORD_SECONDS} bash /tmp/ego-130-record-mcap-track2-poc.sh",
        timeout=1200,
    )
    if code != 0:
        raise SystemExit(f"record failed ({code})")
    session_id = ""
    for line in out.splitlines():
        if line.startswith("TRACK2_POC_RESULT session_id="):
            session_id = line.split("=", 1)[1].strip()
    if not session_id:
        raise SystemExit("no session_id from record")
    return session_id


def upload_on_130(c: paramiko.SSHClient, session_id: str | None = None) -> None:
    extra = f" --session-id {session_id}" if session_id else ""
    code, out = run(
        c,
        f"export PATH=$HOME/.local/bin:$PATH; ego-upload {STATION}{extra} --notify",
        timeout=1800,
    )
    if code != 0:
        raise SystemExit(f"upload failed ({code})")


def ego_process_local() -> None:
    cmd = [str(SCRIPTS / "ego-process"), STATION]
    print(f"\n[34] $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        raise SystemExit(f"ego-process failed ({proc.returncode})")


def verify_local(session_id: str) -> dict:
    stream = ROOT / "data-storage" / "stream" / STATION
    unit_dir = stream / "derived" / session_id
    unit_json = unit_dir / "unit.json"
    result: dict = {"session_id": session_id, "unit_json": str(unit_json), "ok": False}
    if unit_json.is_file():
        data = json.loads(unit_json.read_text())
        result["derive_ms"] = data.get("derive", {}).get("elapsed_ms")
        result["mux_mode"] = data.get("derive", {}).get("mux_mode")
        result["frames"] = data.get("frames")
        videos = list((unit_dir / "videos").glob("*.mp4")) if (unit_dir / "videos").is_dir() else []
        result["mp4_count"] = len(videos)
        result["mp4_bytes"] = sum(v.stat().st_size for v in videos)
        result["ok"] = result["mp4_count"] >= 4 and bool(result.get("frames"))
    ready = stream / "state" / "sessions" / session_id / "session.READY"
    result["session_ready"] = ready.is_file()
    result["ok"] = result["ok"] and result["session_ready"]
    return result


def latest_session_on_130(c: paramiko.SSHClient) -> str:
    _, out = run(
        c,
        "ls -1t /home/server/cache/ego-mcap-track2/segments/sessions 2>/dev/null | head -1",
    )
    return out.strip().splitlines()[-1].strip() if out.strip() else ""


def main() -> int:
    print("==> Track2 E2E start")
    c = connect()
    try:
        provision_upload_script(c)
        if SKIP_RECORD:
            session_id = latest_session_on_130(c)
            print(f"==> SKIP_RECORD: using latest session {session_id}")
        else:
            print(f"==> recording ~{RECORD_SECONDS}s on 130 …")
            session_id = record_on_130(c)
        print(f"==> upload session {session_id}")
        upload_on_130(c, session_id)
    finally:
        c.close()

    print("==> ego-process on 34")
    ego_process_local()

    result = verify_local(session_id)
    print("\n==> E2E RESULT")
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        return 1
    print(f"\n✅ E2E OK session={session_id} derive_ms={result.get('derive_ms')} mp4={result.get('mp4_count')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
