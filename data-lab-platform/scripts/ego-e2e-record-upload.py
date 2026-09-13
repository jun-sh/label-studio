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
WAIT_AFTER_STOP = int(os.environ.get("WAIT_AFTER_STOP", os.environ.get("E2E_WAIT_AFTER_STOP", "25")))
PIPELINE_READY_TIMEOUT = int(os.environ.get("PIPELINE_READY_TIMEOUT", "120"))
# Beep-aligned: timer starts at capture-only (same as phone UI). POST_READY is legacy override only.
POST_READY_SEC = int(os.environ.get("POST_READY_SEC", os.environ.get("E2E_POST_READY_SEC", "0")))
INTER_EPISODE_SEC = int(os.environ.get("INTER_EPISODE_SEC", os.environ.get("E2E_INTER_EPISODE_SEC", "15")))
CAPTURE_BEEP_TIMEOUT = int(os.environ.get("CAPTURE_BEEP_TIMEOUT", "120"))
MCAP_UNIT = "ecs-record-oak-mcap.service"
UPLOAD_UNTIL_COMPLETE = os.environ.get("EGO_UPLOAD_UNTIL_COMPLETE", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
UPLOAD_TIMEOUT = int(os.environ.get("EGO_UPLOAD_UNTIL_COMPLETE_TIMEOUT_S", "600"))
UPLOAD_NOTIFY = os.environ.get("EGO_E2E_UPLOAD_NOTIFY", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
SEGMENT_FINALIZE_TIMEOUT = int(os.environ.get("SEGMENT_FINALIZE_TIMEOUT", "120"))
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
sleep 3
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
        MCAP_UNIT,
        since,
        "oak_output_resolution ok mode=h264",
        PIPELINE_READY_TIMEOUT,
    ):
        _, jout = ssh_run(
            c,
            f"journalctl --user -u {MCAP_UNIT} --since '{since}' --no-pager | tail -20",
            timeout=30,
        )
        log("FAIL h264 pipeline not ready\n" + jout[-1500:])
        ssh_run(
            c,
            f"systemctl --user stop {MCAP_UNIT}; systemctl --user reset-failed {MCAP_UNIT} 2>/dev/null || true",
            timeout=60,
        )
        return None

    if not wait_journal_since(
        c,
        MCAP_UNIT,
        since,
        "capture-only session=",
        CAPTURE_BEEP_TIMEOUT,
    ):
        _, jout = ssh_run(
            c,
            f"journalctl --user -u {MCAP_UNIT} --since '{since}' --no-pager | tail -25",
            timeout=30,
        )
        log("FAIL capture-only/beep not seen\n" + jout[-1500:])
        ssh_run(
            c,
            f"systemctl --user stop {MCAP_UNIT}; systemctl --user reset-failed {MCAP_UNIT} 2>/dev/null || true",
            timeout=60,
        )
        return None

    if POST_READY_SEC > 0:
        log(f"legacy POST_READY {POST_READY_SEC}s after beep …")
        time.sleep(POST_READY_SEC)

    log(f"beep 对齐: 从 capture-only 起计时 {RECORD_SECONDS}s（与手机 UI 一致）")
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


def wait_session_sealed(c: paramiko.SSHClient, session_id: str, timeout: int) -> bool:
    seal = f"{SEG}/sessions/{session_id}/session_seal.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        rc, out = ssh_run(
            c,
            f"""
if [ -f '{seal}' ]; then
  python3 -c "import json; d=json.load(open('{seal}')); print('complete', d.get('complete'), 'count', d.get('segment_count'))"
fi
""",
            timeout=30,
        )
        if rc == 0 and "complete True" in out:
            return True
        time.sleep(2)
    return False


def session_segment_gate(c: paramiko.SSHClient, session_id: str) -> tuple[bool, str, str]:
    rc, out = ssh_run(
        c,
        f"""
export PYTHONPATH=/home/server/workspace/ego-studio/src
/home/server/workspace/ego-studio/.venv/bin/python3 - <<'IN'
from pathlib import Path
from ego_capture_studio.capture.segment_store import (
    check_segment_upload_qc,
    iter_session_segment_dirs,
    read_manifest,
    session_segment_status_summary,
)
root = Path('/home/server/cache/{STATION}/segments')
summary = session_segment_status_summary(root, '{session_id}')
print('corrupt', len(summary.get('corrupt_issues') or []))
for issue in (summary.get('corrupt_issues') or [])[:3]:
    print('corrupt_issue', issue)
for seg in iter_session_segment_dirs(root, '{session_id}'):
    manifest = read_manifest(seg)
    tl = manifest.get('timeline') or {{}}
    ratio = tl.get('ratio')
    if ratio is not None:
        print('timeline_ratio', seg.name, f'{{float(ratio):.4f}}')
        print('timeline_real_fps', seg.name, f'{{float(tl.get("real_fps") or 0):.3f}}')
    ok, issues = check_segment_upload_qc(seg)
    if not ok:
        print('upload_qc_fail', seg.name + ':' + (issues[0] if issues else 'upload_qc_failed'))
        break
IN
""",
        timeout=60,
    )
    if rc != 0:
        return False, f"segment_gate_rc={rc}", out
    corrupt_n = 0
    corrupt_sample = ""
    upload_qc_fail = ""
    for line in out.splitlines():
        if line.startswith("corrupt "):
            try:
                corrupt_n = int(line.split()[1])
            except (IndexError, ValueError):
                pass
        if line.startswith("corrupt_issue "):
            corrupt_sample = line.split("corrupt_issue ", 1)[1]
        if line.startswith("upload_qc_fail "):
            upload_qc_fail = line.split("upload_qc_fail ", 1)[1]
    if corrupt_n > 0:
        return False, f"corrupt_segments={corrupt_n}:{corrupt_sample}", out
    if upload_qc_fail:
        return False, f"upload_qc={upload_qc_fail}", out
    return True, "ok", out


def main() -> int:
    log(
        f"=== 录制+上传 {EPISODES} 段 · {STATION} · {RECORD_SECONDS}s/段 "
        f"(beep对齐 POST_READY={POST_READY_SEC}s ep间隔={INTER_EPISODE_SEC}s) ==="
    )
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

            log(f"session={sid}")
            if not wait_session_sealed(c, sid, SEGMENT_FINALIZE_TIMEOUT):
                log(f"FAIL session seal timeout ({SEGMENT_FINALIZE_TIMEOUT}s)")
                continue
            ok_gate, gate_reason, gate_out = session_segment_gate(c, sid)
            for line in gate_out.splitlines():
                if line.startswith("timeline_ratio ") or line.startswith("timeline_real_fps "):
                    log(line)
            if not ok_gate:
                log(f"FAIL segment gate: {gate_reason}")
                continue

            upload_start = time.time()
            upload_flags = "--until-complete" if UPLOAD_UNTIL_COMPLETE else ""
            notify_flag = "--notify" if UPLOAD_NOTIFY else "--no-notify"
            rc, out = ssh_run(
                c,
                f"""export PATH="$HOME/.local/bin:$PATH"
export SEGMENT_MCAP=1 SEGMENT_FRAME_BIN=0 UPLOAD_PROTOCOL=mcap OAK_H264=1 OAK_HW_JPEG=0
export EGO_UPLOAD_UNTIL_COMPLETE={'1' if UPLOAD_UNTIL_COMPLETE else '0'}
export EGO_UPLOAD_UNTIL_COMPLETE_TIMEOUT_S={UPLOAD_TIMEOUT}
export EGO_UPLOAD_UNTIL_COMPLETE_POLL_S=1
ego-upload {STATION} --session-id {sid} {notify_flag} {upload_flags}""",
                timeout=UPLOAD_TIMEOUT + 60,
            )
            upload_sec = time.time() - upload_start
            log(out.strip()[-1200:])
            if rc != 0 or ("segment_ok" not in out and "uploaded_segments=0" in out):
                log(f"FAIL upload rc={rc} ({upload_sec:.1f}s)")
                continue
            known.add(sid)
            uploaded.append(sid)
            log(f"OK {episode}/{EPISODES} upload={upload_sec:.1f}s")
            episode_ok = True
            time.sleep(INTER_EPISODE_SEC)
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
