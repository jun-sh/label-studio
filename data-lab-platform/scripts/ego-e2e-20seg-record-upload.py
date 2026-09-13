#!/usr/bin/env python3
"""Record + upload MCAP episodes on 130 with varying duration and OAK settle gates."""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from datetime import datetime

import paramiko

HOST, USER = "10.10.10.130", "server"
PASS = os.environ.get("RC_CAPTURE_PASS", "1")
STATION = os.environ.get("STATION_ID", "ego-001")
EPISODES = int(os.environ.get("EPISODES", "20"))
START_EPISODE = int(os.environ.get("START_EPISODE", "1"))
MIN_SEC = int(os.environ.get("MIN_SEC", "30"))
MAX_SEC = int(os.environ.get("MAX_SEC", "60"))
WAIT_AFTER_STOP = int(os.environ.get("WAIT_AFTER_STOP", "25"))
SEGMENT_FINALIZE_TIMEOUT = int(os.environ.get("SEGMENT_FINALIZE_TIMEOUT", "300"))
UPLOAD_UNTIL_COMPLETE = os.environ.get("EGO_UPLOAD_UNTIL_COMPLETE", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
RECORD_ONLY = os.environ.get("EGO_E2E_RECORD_ONLY", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
PIPELINE_READY_TIMEOUT = int(os.environ.get("PIPELINE_READY_TIMEOUT", "120"))
POST_READY_SEC = int(os.environ.get("POST_READY_SEC", "8"))
UPLOAD_TIMEOUT = int(os.environ.get("UPLOAD_TIMEOUT", "3600"))
STOP_TIMEOUT = int(os.environ.get("STOP_TIMEOUT", "600"))
MCAP_WAIT_ROUNDS = int(os.environ.get("MCAP_WAIT_ROUNDS", "36"))
INTER_EPISODE_COOLDOWN_SEC = int(os.environ.get("INTER_EPISODE_COOLDOWN_SEC", "15"))
INTER_EPISODE_SETTLE_SEC = int(os.environ.get("INTER_EPISODE_SETTLE_SEC", "10"))
WARMUP_CAPTURE_TIMEOUT = int(os.environ.get("WARMUP_CAPTURE_TIMEOUT", "90"))
WARMUP_MIN_FRAMES = int(os.environ.get("WARMUP_MIN_FRAMES", "30"))
MIN_CAPTURE_FPS = float(os.environ.get("MIN_CAPTURE_FPS", "5.0"))
MIN_DONE_FPS = float(os.environ.get("MIN_DONE_FPS", "5.0"))
MIN_DONE_FRAME_RATIO = float(os.environ.get("MIN_DONE_FRAME_RATIO", "0.45"))
STALL_NO_PROGRESS_SEC = int(os.environ.get("STALL_NO_PROGRESS_SEC", "45"))
RECORD_POLL_SEC = int(os.environ.get("RECORD_POLL_SEC", "15"))
METRICS_JSON = os.environ.get("METRICS_JSON", "")
RUN_LOG = os.environ.get("RUN_LOG", "")
DURATIONS_CSV = os.environ.get("DURATIONS_CSV", "").strip()
MCAP_UNIT = "ecs-record-oak-mcap.service"
SEG = f"/home/server/cache/{STATION}/segments"
CKPT = f"/home/server/cache/{STATION}/checkpoint.json"
ACTIVE = f"/tmp/{STATION}-mcap-active"

CAPTURED_RE = re.compile(
    r"captured=(\d+) capture_fps=([\d.]+).*session=(sess_[a-f0-9]+)"
)
DONE_RE = re.compile(
    r"Done\. (\d+) frames in ([\d.]+)s \(([\d.]+) fps\) session=(sess_[a-f0-9]+)"
)


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if RUN_LOG:
        with open(RUN_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def append_metric(obj: dict) -> None:
    if not METRICS_JSON:
        return
    with open(METRICS_JSON, "a", encoding="utf-8") as f:
        f.write("    " + json.dumps(obj, ensure_ascii=False) + ",\n")


def ssh_run(c: paramiko.SSHClient, script: str, timeout: int = 600) -> tuple[int, str]:
    _, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{script}\nREMOTE", timeout=timeout)
    out = (o.read() + e.read()).decode()
    return o.channel.recv_exit_status(), out


def journal_since(c: paramiko.SSHClient) -> str:
    _, out = ssh_run(c, "date '+%Y-%m-%d %H:%M:%S'", timeout=10)
    return out.strip()


def fetch_journal_since(c: paramiko.SSHClient, since: str) -> str:
    _, out = ssh_run(
        c,
        f"journalctl --user -u {MCAP_UNIT} --since '{since}' --no-pager 2>/dev/null",
        timeout=60,
    )
    return out


def wait_journal_since(
    c: paramiko.SSHClient, since: str, marker: str, timeout: int
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rc, out = ssh_run(
            c,
            f"journalctl --user -u {MCAP_UNIT} --since '{since}' --no-pager 2>/dev/null | grep -F '{marker}' | tail -1",
            timeout=30,
        )
        if rc == 0 and marker in out:
            return True
        time.sleep(2)
    return False


def latest_captured_for_session(journal: str, session_id: str) -> tuple[int, float] | None:
    best: tuple[int, float] | None = None
    for line in journal.splitlines():
        if session_id not in line:
            continue
        m = CAPTURED_RE.search(line)
        if m and m.group(3) == session_id:
            best = (int(m.group(1)), float(m.group(2)))
    return best


def parse_done_line(journal: str, session_id: str) -> tuple[int, float, float] | None:
    for line in reversed(journal.splitlines()):
        if session_id not in line:
            continue
        m = DONE_RE.search(line)
        if m and m.group(4) == session_id:
            return int(m.group(1)), float(m.group(2)), float(m.group(3))
    return None


def infer_session_from_journal(journal: str) -> str | None:
    for line in reversed(journal.splitlines()):
        m = re.search(r"capture-only session=(sess_[a-f0-9]+)", line)
        if m:
            return m.group(1)
        m = re.search(
            r"camera_intrinsics OK .* path=.*/sessions/(sess_[a-f0-9]+)/meta/",
            line,
        )
        if m:
            return m.group(1)
    return None


def wait_session_started(c: paramiko.SSHClient, since: str, timeout: int = 60) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        journal = fetch_journal_since(c, since)
        sid = infer_session_from_journal(journal)
        if sid:
            return sid
        time.sleep(2)
    return None


def prepare_capture_stack(c: paramiko.SSHClient, *, label: str = "准备采集栈") -> bool:
    log(f"{label}: 停 preview/mcap → 释放 OAK")
    rc, out = ssh_run(
        c,
        f"""
set -eo pipefail
systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
systemctl --user stop ecs-record-oak-mcap.service 2>/dev/null || true
systemctl --user stop ecs-record-oak-stream.service 2>/dev/null || true
sleep 2
pkill -f 'ego_capture_studio|record_oak_stream|preview_standby' 2>/dev/null || true
sleep {INTER_EPISODE_SETTLE_SEC}
systemctl --user reset-failed ecs-record-oak-mcap.service 2>/dev/null || true
rm -rf {ACTIVE}
if lsusb | grep -q '03e7:f63c'; then
  cd ~/workspace/ego-studio && .venv/bin/python -m ego_capture_studio.tools.oak_boot_from_bootloader || true
  sleep 3
fi
echo ready
""",
        timeout=180,
    )
    if "ready" not in out:
        log(f"FAIL prepare rc={rc}\n{out[-800:]}")
        return False
    if INTER_EPISODE_COOLDOWN_SEC > 0:
        log(f"段间冷却 {INTER_EPISODE_COOLDOWN_SEC}s …")
        time.sleep(INTER_EPISODE_COOLDOWN_SEC)
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


def stop_capture_service(c: paramiko.SSHClient) -> None:
    ssh_run(
        c,
        f"systemctl --user stop {MCAP_UNIT}; sleep {WAIT_AFTER_STOP}; "
        f"systemctl --user reset-failed {MCAP_UNIT} 2>/dev/null || true",
        timeout=STOP_TIMEOUT,
    )


def wait_capture_warmup(
    c: paramiko.SSHClient, since: str, session_id: str
) -> tuple[bool, str]:
    deadline = time.time() + WARMUP_CAPTURE_TIMEOUT
    while time.time() < deadline:
        journal = fetch_journal_since(c, since)
        cap = latest_captured_for_session(journal, session_id)
        if cap and cap[0] >= WARMUP_MIN_FRAMES and cap[1] >= MIN_CAPTURE_FPS:
            log(
                f"warmup OK session={session_id[:18]}… captured={cap[0]} fps={cap[1]:.2f}"
            )
            return True, session_id
        if cap:
            log(
                f"warmup progress captured={cap[0]}/{WARMUP_MIN_FRAMES} fps={cap[1]:.2f}"
            )
        time.sleep(3)
    return False, session_id


def record_with_stall_watch(
    c: paramiko.SSHClient, since: str, session_id: str, record_seconds: int
) -> tuple[bool, float, str]:
    record_start = time.time()
    deadline = record_start + record_seconds
    last_count = WARMUP_MIN_FRAMES
    last_progress = time.time()

    while time.time() < deadline:
        sleep_for = min(RECORD_POLL_SEC, max(1, int(deadline - time.time())))
        time.sleep(sleep_for)
        journal = fetch_journal_since(c, since)
        cap = latest_captured_for_session(journal, session_id)
        if cap and cap[0] > last_count:
            last_count = cap[0]
            last_progress = time.time()
            continue
        if time.time() - last_progress >= STALL_NO_PROGRESS_SEC:
            log(
                f"STALL abort session={session_id[:18]}… captured={last_count} "
                f"no_progress>{STALL_NO_PROGRESS_SEC}s"
            )
            stop_capture_service(c)
            return False, time.time() - record_start, "stall_during_record"

    stop_capture_service(c)
    return True, time.time() - record_start, "ok"


def validate_done_line(
    c: paramiko.SSHClient, since: str, session_id: str, record_seconds: int
) -> tuple[bool, str, dict]:
    journal = fetch_journal_since(c, since)
    done = parse_done_line(journal, session_id)
    if not done:
        return False, "missing_done_line", {}
    frames, elapsed, fps = done
    min_frames = max(WARMUP_MIN_FRAMES, int(record_seconds * MIN_DONE_FRAME_RATIO * MIN_CAPTURE_FPS))
    meta = {
        "done_frames": frames,
        "done_elapsed_s": round(elapsed, 2),
        "done_fps": round(fps, 3),
        "min_frames_gate": min_frames,
    }
    if fps < MIN_DONE_FPS:
        return False, f"done_fps_too_low:{fps:.2f}<{MIN_DONE_FPS}", meta
    if frames < min_frames:
        return False, f"done_frames_too_low:{frames}<{min_frames}", meta
    return True, "ok", meta


def wait_session_sealed(c: paramiko.SSHClient, session_id: str, timeout: int) -> bool:
    """Wait until session_seal.json exists with complete=true on 130."""
    seal = f"/home/server/cache/{STATION}/segments/sessions/{session_id}/session_seal.json"
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
        time.sleep(3)
    return False


def session_segment_gate(c: paramiko.SSHClient, session_id: str) -> tuple[bool, str]:
    """Check 130 segment manifests for CORRUPT / incomplete finalize before upload."""
    rc, out = ssh_run(
        c,
        f"""
export PYTHONPATH=/home/server/workspace/ego-studio/src
/home/server/workspace/ego-studio/.venv/bin/python3 - <<'IN'
from ego_capture_studio.capture.segment_store import session_segment_status_summary
summary = session_segment_status_summary(
    __import__('pathlib').Path('/home/server/cache/{STATION}/segments'),
    '{session_id}',
)
print('pending', summary.get('pending_closed'))
print('corrupt', len(summary.get('corrupt_issues') or []))
for issue in (summary.get('corrupt_issues') or [])[:5]:
    print('corrupt_issue', issue)
print('status', summary.get('status_counts'))
IN
""",
        timeout=60,
    )
    if rc != 0:
        return False, f"segment_gate_rc={rc}"
    corrupt_n = 0
    corrupt_sample = ""
    for line in out.splitlines():
        if line.startswith("corrupt "):
            try:
                corrupt_n = int(line.split()[1])
            except (IndexError, ValueError):
                pass
        if line.startswith("corrupt_issue "):
            corrupt_sample = line.split("corrupt_issue ", 1)[1]
    if corrupt_n > 0:
        return False, f"corrupt_segments={corrupt_n}:{corrupt_sample}"
    return True, "ok"


def record_one_episode(
    c: paramiko.SSHClient, known: set[str], record_seconds: int
) -> tuple[str | None, str | None]:
    since = journal_since(c)
    ep_start = time.time()
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
    startup_sec = time.time() - ep_start
    if rc != 0:
        log(f"FAIL start rc={rc}\n{out.strip()[-500:]}")
        return None, "start_failed"

    pipeline_ready_start = time.time()
    if not wait_journal_since(
        c, since, "oak_output_resolution ok mode=h264", PIPELINE_READY_TIMEOUT
    ):
        _, jout = ssh_run(
            c,
            f"journalctl --user -u {MCAP_UNIT} --since '{since}' --no-pager | tail -20",
            timeout=30,
        )
        log("FAIL h264 pipeline not ready\n" + jout[-1500:])
        stop_capture_service(c)
        return None, "pipeline_not_ready"
    pipeline_ready_sec = time.time() - pipeline_ready_start

    session_id = wait_session_started(c, since, timeout=90)
    if not session_id:
        log("FAIL capture-only session not seen in journal")
        stop_capture_service(c)
        return None, "session_id_missing"

    if POST_READY_SEC > 0:
        log(f"pipeline ready ({pipeline_ready_sec:.1f}s), post-ready {POST_READY_SEC}s …")
        time.sleep(POST_READY_SEC)

    ok_warmup, _ = wait_capture_warmup(c, since, session_id)
    if not ok_warmup:
        log(f"FAIL warmup timeout session={session_id}")
        stop_capture_service(c)
        return None, "warmup_timeout"

    log(f"recording {record_seconds}s session={session_id}")
    ok_record, record_actual_sec, record_reason = record_with_stall_watch(
        c, since, session_id, record_seconds
    )
    if not ok_record:
        return None, record_reason

    ok_done, done_reason, done_meta = validate_done_line(
        c, since, session_id, record_seconds
    )
    if not ok_done:
        log(f"FAIL done gate: {done_reason} meta={done_meta}")
        return None, done_reason

    sid = None
    for wait_i in range(MCAP_WAIT_ROUNDS):
        sid = latest_new_session(c, known)
        if sid:
            break
        log(f"等待 segment.mcap ({wait_i + 1}/{MCAP_WAIT_ROUNDS})...")
        time.sleep(5)
    if not sid:
        _, jout = ssh_run(
            c, f"journalctl --user -u {MCAP_UNIT} -n 25 --no-pager", timeout=30
        )
        log("FAIL no mcap\n" + jout[-2000:])
        return None, "mcap_missing"

    stop_sec = WAIT_AFTER_STOP
    _episode_timing[sid] = {
        "record_seconds_target": record_seconds,
        "startup_sec": round(startup_sec, 2),
        "pipeline_ready_sec": round(pipeline_ready_sec, 2),
        "record_sec": round(record_actual_sec, 2),
        "stop_finalize_sec": round(stop_sec, 2),
        **done_meta,
    }
    return sid, None


_episode_timing: dict[str, dict] = {}


def load_durations() -> list[int]:
    if DURATIONS_CSV:
        vals = [int(x.strip()) for x in DURATIONS_CSV.split(",") if x.strip()]
        if len(vals) < EPISODES:
            raise SystemExit(
                f"DURATIONS_CSV has {len(vals)} entries, need EPISODES={EPISODES}"
            )
        return vals[:EPISODES]
    return [random.randint(MIN_SEC, MAX_SEC) for _ in range(EPISODES)]


def main() -> int:
    if START_EPISODE < 1 or START_EPISODE > EPISODES:
        log(f"FAIL invalid START_EPISODE={START_EPISODE} EPISODES={EPISODES}")
        return 2

    all_durations = load_durations()
    durations = all_durations[START_EPISODE - 1 :]
    remaining = len(durations)
    log(
        f"=== 录制+上传 {remaining} 段 (Ep {START_EPISODE}-{EPISODES}) · {STATION} · "
        f"gates: warmup>={WARMUP_MIN_FRAMES}@{MIN_CAPTURE_FPS}fps "
        f"done>={MIN_DONE_FPS}fps cooldown={INTER_EPISODE_COOLDOWN_SEC}s ==="
    )
    log(f"时长计划[{START_EPISODE}-{EPISODES}]: {durations}")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=30)
    known: set[str] = set()
    uploaded: list[str] = []

    if not prepare_capture_stack(c):
        return 1

    for offset, record_seconds in enumerate(durations):
        episode = START_EPISODE + offset
        if episode > 1:
            if not prepare_capture_stack(c, label=f"Ep {episode} 段间 settle"):
                return 1

        log(f"\n--- Episode {episode}/{EPISODES} · 目标 {record_seconds}s ---")
        episode_ok = False
        for attempt in range(1, 4):
            if attempt > 1:
                log(f"重试 episode {episode} ({attempt}/3)")
                if not prepare_capture_stack(c, label="重试 settle"):
                    return 1

            ep_wall_start = time.time()
            sid, fail_reason = record_one_episode(c, known, record_seconds)
            if not sid:
                log(f"attempt {attempt} failed: {fail_reason}")
                append_metric(
                    {
                        "episode": episode,
                        "status": "record_fail",
                        "reason": fail_reason,
                        "record_seconds_target": record_seconds,
                        "attempt": attempt,
                    }
                )
                continue

            known.add(sid)
            log(f"session={sid}")
            if not wait_session_sealed(c, sid, SEGMENT_FINALIZE_TIMEOUT):
                log(f"FAIL session seal timeout after {SEGMENT_FINALIZE_TIMEOUT}s")
                append_metric(
                    {
                        "episode": episode,
                        "session": sid,
                        "status": "seal_timeout",
                        "record_seconds": record_seconds,
                    }
                )
                return 1
            ok_gate, gate_reason = session_segment_gate(c, sid)
            if not ok_gate:
                log(f"FAIL segment gate: {gate_reason}")
                append_metric(
                    {
                        "episode": episode,
                        "session": sid,
                        "status": "segment_gate_fail",
                        "reason": gate_reason,
                        "record_seconds": record_seconds,
                    }
                )
                return 1
            if RECORD_ONLY:
                uploaded.append(sid)
                append_metric(
                    {
                        "episode": episode,
                        "session": sid,
                        "status": "record_only_ok",
                        "record_seconds_target": record_seconds,
                    }
                )
                log(f"OK {episode}/{EPISODES} record-only session={sid}")
                episode_ok = True
                time.sleep(4)
                break
            upload_start = time.time()
            upload_flags = "--until-complete" if UPLOAD_UNTIL_COMPLETE else ""
            rc, out = ssh_run(
                c,
                f"""export PATH="$HOME/.local/bin:$PATH"
export SEGMENT_MCAP=1 SEGMENT_FRAME_BIN=0 UPLOAD_PROTOCOL=mcap OAK_H264=1 OAK_HW_JPEG=0
export EGO_UPLOAD_UNTIL_COMPLETE={'1' if UPLOAD_UNTIL_COMPLETE else '0'}
ego-upload {STATION} --session-id {sid} --notify {upload_flags}""",
                timeout=UPLOAD_TIMEOUT,
            )
            upload_sec = time.time() - upload_start
            log(out.strip()[-1200:])
            if rc != 0 or ("segment_ok" not in out and "uploaded_segments=0" in out):
                log(f"FAIL upload rc={rc}")
                append_metric(
                    {
                        "episode": episode,
                        "session": sid,
                        "status": "upload_fail",
                        "record_seconds": record_seconds,
                        "upload_sec": round(upload_sec, 2),
                    }
                )
                return 1

            uploaded.append(sid)
            timing = _episode_timing.get(sid, {})
            total_ep_sec = time.time() - ep_wall_start
            metric = {
                "episode": episode,
                "session": sid,
                "status": "ok",
                "record_seconds_target": record_seconds,
                **timing,
                "upload_sec": round(upload_sec, 2),
                "total_episode_sec": round(total_ep_sec, 2),
            }
            append_metric(metric)
            log(
                f"OK {episode}/{EPISODES} | startup={timing.get('startup_sec')}s "
                f"pipeline={timing.get('pipeline_ready_sec')}s record={timing.get('record_sec')}s "
                f"done_fps={timing.get('done_fps')} done_frames={timing.get('done_frames')} "
                f"upload={upload_sec:.1f}s total={total_ep_sec:.1f}s"
            )
            episode_ok = True
            time.sleep(4)
            break

        if not episode_ok:
            log(f"FAIL episode {episode} after 3 attempts")
            return 1

    ssh_run(c, "systemctl --user start ecs-preview-standby.service 2>/dev/null || true", timeout=30)
    c.close()
    expected = EPISODES - START_EPISODE + 1
    log(f"\n=== 上传完成 {len(uploaded)}/{expected} (Ep {START_EPISODE}-{EPISODES}) ===")
    return 0 if len(uploaded) == expected else 1


if __name__ == "__main__":
    sys.exit(main())
