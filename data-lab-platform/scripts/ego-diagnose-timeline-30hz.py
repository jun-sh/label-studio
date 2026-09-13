#!/usr/bin/env python3
"""Diagnose 130 timeline coherence: 1×60s vs 6×60s (apples-to-apples segment gate)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import paramiko

HOST, USER = "10.10.10.130", "server"
PASS = os.environ.get("RC_CAPTURE_PASS", "1")
STATION = os.environ.get("STATION_ID", "ego-001")
SEG = f"/home/server/cache/{STATION}/segments"
SCRIPT_DIR = Path(__file__).resolve().parent
DATALAB = SCRIPT_DIR.parent.parent
LOG_DIR = DATALAB / "data-storage" / "logs"
REPORT_DIR = DATALAB / "data-lab-platform" / "docs" / "moe-evidence"
REPORT_DIR = DATALAB / "data-lab-platform" / "docs" / "m0-evidence"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
RUN_LOG = LOG_DIR / f"diagnose-timeline-30hz-{STAMP}.log"
REPORT_JSON = REPORT_DIR / f"diagnose-timeline-30hz-{STAMP}.json"


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with open(RUN_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ssh_run(c: paramiko.SSHClient, script: str, timeout: int = 600) -> tuple[int, str]:
    _, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{script}\nREMOTE", timeout=timeout)
    out = (o.read() + e.read()).decode()
    return o.channel.recv_exit_status(), out


def prepare_stack(c: paramiko.SSHClient) -> None:
    ssh_run(
        c,
        f"""
set -euo pipefail
systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
systemctl --user stop ecs-record-oak-mcap.service 2>/dev/null || true
sleep 2
pkill -f 'ego_capture_studio|record_oak_stream|preview_standby' 2>/dev/null || true
sleep 3
rm -rf /tmp/{STATION}-mcap-active
systemctl --user reset-failed ecs-record-oak-mcap.service 2>/dev/null || true
echo ready
""",
        timeout=90,
    )


def clear_segments(c: paramiko.SSHClient) -> None:
    ssh_run(
        c,
        f"""
set -euo pipefail
systemctl --user stop ecs-record-oak-mcap.service 2>/dev/null || true
rm -rf {SEG}/sessions {SEG}/registry.json {SEG}/checkpoint.json
mkdir -p {SEG}
echo cleared
""",
        timeout=60,
    )


def segment_timeline_report(c: paramiko.SSHClient, session_id: str) -> dict:
    rc, out = ssh_run(
        c,
        f"""
export PYTHONPATH=/home/server/workspace/ego-studio/src
/home/server/workspace/ego-studio/.venv/bin/python3 - <<'PY'
import json
from pathlib import Path
from ego_capture_studio.capture.segment_store import (
    check_segment_integrity,
    iter_session_segment_dirs,
    read_manifest,
    session_segment_status_summary,
)

root = Path("{SEG}")
sid = "{session_id}"
summary = session_segment_status_summary(root, sid)
rows = []
for seg_dir in iter_session_segment_dirs(root, sid):
    manifest = read_manifest(seg_dir)
    tl = manifest.get("timeline") or {{}}
    ok, issues = check_segment_integrity(seg_dir)
    ch = manifest.get("capture_health") or {{}}
    rows.append({{
        "segment": seg_dir.name,
        "frame_count": manifest.get("frame_count"),
        "timeline": tl,
        "integrity_ok": ok,
        "issues": issues,
        "ring_ovf": (ch.get("ring_ovf") or {{}}),
        "quad_skew": ch.get("quad_skew_events"),
    }})
print(json.dumps({{"summary": summary, "segments": rows}}, ensure_ascii=False))
PY
""",
        timeout=120,
    )
    if rc != 0:
        return {"error": out[-2000:]}
    try:
        return json.loads(out.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return {"error": "parse_failed", "raw": out[-3000:]}


def run_record_phase(label: str, episodes: int, durations_csv: str) -> dict:
    log(f"=== {label}: {episodes} ep durations={durations_csv} ===")
    env = os.environ.copy()
    env.update(
        {
            "RC_CAPTURE_PASS": PASS,
            "EPISODES": str(episodes),
            "DURATIONS_CSV": durations_csv,
            "MIN_SEC": "60",
            "MAX_SEC": "60",
            "POST_READY_SEC": "5",
            "WAIT_AFTER_STOP": "22",
            "INTER_EPISODE_COOLDOWN_SEC": "15",
            "SEGMENT_FINALIZE_TIMEOUT": "180",
            "EGO_UPLOAD_UNTIL_COMPLETE": "0",
            "RUN_LOG": str(RUN_LOG),
        }
    )
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, "-u", str(SCRIPT_DIR / "ego-e2e-20seg-record-upload.py")],
        env=env,
        cwd=str(DATALAB),
        capture_output=False,
    )
    elapsed = time.time() - t0
    return {"label": label, "exit_code": proc.returncode, "elapsed_s": round(elapsed, 1)}


def collect_sessions(c: paramiko.SSHClient) -> list[str]:
    rc, out = ssh_run(
        c,
        f"ls -1dt {SEG}/sessions/sess_* 2>/dev/null | xargs -n1 basename | head -20",
        timeout=30,
    )
    if rc != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip().startswith("sess_")]


def main() -> int:
    log(f"diagnose timeline 30Hz · log={RUN_LOG}")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=30)

    rc, usb = ssh_run(c, "lsusb | grep -i 03e7 || true", timeout=15)
    if "03e7" not in usb:
        log("FAIL: OAK USB not found")
        return 2
    log(f"OAK: {usb.strip().splitlines()[-1]}")

    report: dict = {"stamp": STAMP, "tests": [], "log": str(RUN_LOG)}

    # --- Test A: single 60s ---
    clear_segments(c)
    prepare_stack(c)
    a = run_record_phase("A_single_60s", 1, "60")
    sessions_a = collect_sessions(c)
    a_detail = []
    for sid in sessions_a:
        a_detail.append({"session_id": sid, **segment_timeline_report(c, sid)})
    a["sessions"] = a_detail
    report["tests"].append(a)
    log(f"A exit={a['exit_code']} sessions={len(sessions_a)}")

    # --- Test B: 6×60s (reproduce stress ep6 pattern) ---
    clear_segments(c)
    prepare_stack(c)
    b = run_record_phase("B_six_60s", 6, ",".join(["60"] * 6))
    sessions_b = collect_sessions(c)
    b_detail = []
    for sid in sessions_b:
        b_detail.append({"session_id": sid, **segment_timeline_report(c, sid)})
    b["sessions"] = b_detail
    report["tests"].append(b)
    log(f"B exit={b['exit_code']} sessions={len(sessions_b)}")

    # Summary interpretation
    def worst_ratio(test: dict) -> float | None:
        best = None
        for sess in test.get("sessions") or []:
            for seg in sess.get("segments") or []:
                tl = seg.get("timeline") or {}
                r = tl.get("ratio")
                if r is None:
                    continue
                best = r if best is None else max(best, float(r))
        return best

    report["interpretation"] = {
        "A_single_worst_ratio": worst_ratio(a),
        "B_six_worst_ratio": worst_ratio(b),
        "A_passed_gate": a["exit_code"] == 0,
        "B_passed_gate": b["exit_code"] == 0,
        "hypothesis": (
            "single_ok_multi_fail →启停/热累积"
            if a["exit_code"] == 0 and b["exit_code"] != 0
            else (
                "both_fail → FSYNC/曝光/阈值"
                if a["exit_code"] != 0
                else "both_ok → stress-20min 失败为更长 clip(>60s)或方差"
            )
        ),
    }

    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"report={REPORT_JSON}")
    log(f"interpretation: {report['interpretation']}")
    c.close()
    return 0 if a["exit_code"] == 0 and b["exit_code"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
