#!/usr/bin/env python3
"""Record + upload MCAP episodes on 130 via SSH (ego-001 production path)."""
from __future__ import annotations

import os
import sys
import time

import paramiko

HOST, USER = "10.10.10.130", "server"
PASS = os.environ.get("RC_CAPTURE_PASS", "1")
STATION = os.environ.get("STATION_ID", "ego-001")
EPISODES = int(os.environ.get("EPISODES", os.environ.get("E2E_EPISODES", "10")))
RECORD_SECONDS = int(os.environ.get("RECORD_SECONDS", os.environ.get("E2E_RECORD_SECONDS", "50")))
WAIT_AFTER_STOP = int(os.environ.get("WAIT_AFTER_STOP", os.environ.get("E2E_WAIT_AFTER_STOP", "20")))
PIPELINE_READY_TIMEOUT = int(os.environ.get("PIPELINE_READY_TIMEOUT", "120"))
POST_READY_SEC = int(os.environ.get("POST_READY_SEC", "8"))
SEG = f"/home/server/cache/{STATION}/segments"
CKPT = f"/home/server/cache/{STATION}/checkpoint.json"
ACTIVE = f"/tmp/{STATION}-mcap-active"


def log(msg: str) -> None:
    print(msg, flush=True)


def ssh_run(c: paramiko.SSHClient, script: str, timeout: int = 600) -> tuple[int, str]:
    _, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{script}\nREMOTE", timeout=timeout)
    out = (o.read() + e.read()).decode()
    return o.channel.recv_exit_status(), out


def journal_since(c: paramiko.SSHClient) -> str:
    _, out = ssh_run(c, "date '+%Y-%m-%d %H:%M:%S'", timeout=10)
    return out.strip()


def wait_journal_since(
    c: paramiko.SSHClient, unit: str, since: str, marker: str, timeout: int
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rc, out = ssh_run(
            c,
            f"journalctl --user -u {unit} --since '{since}' --no-pager 2>/dev/null | grep -F '{marker}' | tail -1",
            timeout=30,
        )
        if rc == 0 and marker in out:
            return True
        time.sleep(2)
    return False


def prepare_capture_stack(c: paramiko.SSHClient) -> bool:
    log("准备采集栈: 停 preview/mcap → 释放 OAK → 清 active root")
    rc, out = ssh_run(
        c,
        f"""
set -eo pipefail
systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
systemctl --user stop ecs-record-oak-mcap.service 2>/dev/null || true
systemctl --user stop ecs-record-oak-stream.service 2>/dev/null || true
sleep 2
pkill -f 'ego_capture_studio|record_oak_stream|preview_standby' 2>/dev/null || true
sleep 5
systemctl --user reset-failed ecs-record-oak-mcap.service 2>/dev/null || true
rm -rf {ACTIVE}
echo ready
""",
        timeout=90,
    )
    if "ready" not in out:
        log(f"FAIL prepare rc={rc}\n{out[-800:]}")
        return False
    return True


def latest_new_session(c: paramiko.SSHClient, known: set[str]) -> str | None:
    rc, out = ssh_run(
        c,
        f"""
for d in $(ls -1dt {SEG}/sessions/sess_* 2>/dev/null); do
  if find "$d" -name segment.mcap -size +1M 2>/dev/null | grep -q .; then
    basename "$d"; exit 0
  fi
done
exit 1
""",
        timeout=30,
    )
    if rc != 0:
        return None
    for line in out.strip().splitlines():
        sid = line.strip()
        if sid.startswith("sess_") and sid not in known:
            return sid
    return None


def record_one_episode(c: paramiko.SSHClient, known: set[str]) -> str | None:
    since = journal_since(c)
    rc, out = ssh_run(
        c,
        f"""
set -euo pipefail
systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
systemctl --user stop ecs-record-oak-mcap.service 2>/dev/null || true
sleep 2
systemctl --user reset-failed ecs-record-oak-mcap.service 2>/dev/null || true
rm -f {CKPT} {SEG}/checkpoint.json {SEG}/strict_emit_ts.json
systemctl --user start ecs-record-oak-mcap.service
systemctl --user is-active ecs-record-oak-mcap.service
""",
        timeout=120,
    )
    log(out.strip())
    if rc != 0:
        log(f"FAIL start rc={rc}")
        return None

    if not wait_journal_since(
        c,
        "ecs-record-oak-mcap.service",
        since,
        "oak_output_resolution ok mode=h264",
        PIPELINE_READY_TIMEOUT,
    ):
        _, jout = ssh_run(
            c,
            f"journalctl --user -u ecs-record-oak-mcap.service --since '{since}' --no-pager | tail -20",
            timeout=30,
        )
        log("FAIL h264 pipeline not ready\n" + jout[-1500:])
        ssh_run(c, "systemctl --user stop ecs-record-oak-mcap.service; systemctl --user reset-failed ecs-record-oak-mcap.service 2>/dev/null || true", timeout=60)
        return None

    if not wait_journal_since(
        c,
        "ecs-record-oak-mcap.service",
        since,
        "capture-only session=",
        30,
    ):
        log("WARN capture-only marker missing; continuing after post-ready wait")

    log(f"pipeline h264 ready, 缓冲 {POST_READY_SEC}s 后开始计时 {RECORD_SECONDS}s")
    time.sleep(POST_READY_SEC)
    log(f"录制 {RECORD_SECONDS}s ...")
    time.sleep(RECORD_SECONDS)
    ssh_run(
        c,
        f"systemctl --user stop ecs-record-oak-mcap.service; sleep {WAIT_AFTER_STOP}; systemctl --user reset-failed ecs-record-oak-mcap.service 2>/dev/null || true",
        timeout=240,
    )

    sid = None
    for wait_i in range(12):
        sid = latest_new_session(c, known)
        if sid:
            break
        log(f"等待 segment.mcap ({wait_i + 1}/12)...")
        time.sleep(5)
    if not sid:
        _, jout = ssh_run(
            c, "journalctl --user -u ecs-record-oak-mcap.service -n 25 --no-pager", timeout=30
        )
        log("FAIL no mcap\n" + jout[-2000:])
    return sid


def main() -> int:
    log(f"=== 录制+上传 {EPISODES} 段 · {STATION} · {RECORD_SECONDS}s/段 ===")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=30)
    known: set[str] = set()
    uploaded: list[str] = []

    if not prepare_capture_stack(c):
        return 1

    episode = 1
    while episode <= EPISODES:
        log(f"\n--- Episode {episode}/{EPISODES} ---")
        episode_ok = False
        for attempt in range(1, 4):
            if attempt > 1:
                log(f"重试 episode {episode} ({attempt}/3)")
                if not prepare_capture_stack(c):
                    return 1

            sid = record_one_episode(c, known)
            if not sid:
                continue

            known.add(sid)
            log(f"session={sid}")
            rc, out = ssh_run(
                c,
                f"""export PATH="$HOME/.local/bin:$PATH"
export SEGMENT_MCAP=1 SEGMENT_FRAME_BIN=0 UPLOAD_PROTOCOL=mcap OAK_H264=1 OAK_HW_JPEG=0
ego-upload {STATION} --session-id {sid} --notify""",
                timeout=600,
            )
            log(out.strip())
            if rc != 0 or ("segment_ok" not in out and "uploaded_segments=0" in out):
                log(f"FAIL upload rc={rc}")
                return 1
            uploaded.append(sid)
            log(f"OK {episode}/{EPISODES}")
            episode_ok = True
            time.sleep(4)
            break

        if not episode_ok:
            log(f"FAIL episode {episode} after 3 attempts")
            return 1
        episode += 1

    ssh_run(c, "systemctl --user start ecs-preview-standby.service 2>/dev/null || true", timeout=30)
    c.close()
    log(f"\n=== 上传完成 {len(uploaded)}/{EPISODES} ===")
    return 0 if len(uploaded) == EPISODES else 1


if __name__ == "__main__":
    sys.exit(main())
