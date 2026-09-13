#!/usr/bin/env python3
"""Timeline soak: record N×45s on 130, report corpus (3%) vs stats (5%) fail rates."""
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
EPISODES = int(os.environ.get("EPISODES", "20"))
DURATIONS_CSV = os.environ.get(
    "STRESS_DURATIONS_CSV",
    ",".join(["45"] * EPISODES),
)

SCRIPT_DIR = Path(__file__).resolve().parent
DATALAB = SCRIPT_DIR.parent.parent
LOG_DIR = DATALAB / "data-storage" / "logs"
REPORT_DIR = DATALAB / "data-lab-platform" / "docs" / "m0-evidence"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
RUN_LOG = Path(os.environ.get("RUN_LOG", LOG_DIR / f"soak-timeline-{STAMP}.log"))


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(RUN_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ssh_run(c: paramiko.SSHClient, script: str, timeout: int = 600) -> tuple[int, str]:
    _, o, e = c.exec_command(f"bash -s <<'REMOTE'\n{script}\nREMOTE", timeout=timeout)
    out = (o.read() + e.read()).decode()
    return o.channel.recv_exit_status(), out


def clear_segments(c: paramiko.SSHClient) -> None:
    ssh_run(
        c,
        f"""
set -euo pipefail
systemctl --user stop ecs-record-oak-mcap.service 2>/dev/null || true
systemctl --user stop ecs-preview-standby.service 2>/dev/null || true
sleep 2
rm -rf {SEG}/sessions {SEG}/registry.json {SEG}/checkpoint.json {SEG}/strict_emit_ts.json
mkdir -p {SEG}
echo cleared
""",
        timeout=60,
    )


def timeline_tier_report(c: paramiko.SSHClient, session_id: str) -> dict:
    rc, out = ssh_run(
        c,
        f"""
export PYTHONPATH=/home/server/workspace/ego-studio/src
/home/server/workspace/ego-studio/.venv/bin/python3 - <<'PY'
import json
from pathlib import Path
from ego_capture_studio.capture.segment_store import (
    iter_session_segment_dirs,
    read_manifest,
    session_segment_status_summary,
)
from ego_capture_studio.capture.strict_fps_gate import (
    timeline_integrity_issues,
    timeline_stats_issues,
)

root = Path("{SEG}")
sid = "{session_id}"
summary = session_segment_status_summary(root, sid)
segments = []
for seg_dir in iter_session_segment_dirs(root, sid):
    manifest = read_manifest(seg_dir)
    fc = int(manifest.get("frame_count") or 0)
    tl = manifest.get("timeline") or {{}}
    corpus_issues = timeline_integrity_issues(tl, frame_count=fc)
    stats_issues = timeline_stats_issues(tl, frame_count=fc)
    segments.append({{
        "segment": seg_dir.name,
        "frame_count": fc,
        "ratio": tl.get("ratio"),
        "corpus_ok": not corpus_issues,
        "stats_ok": not stats_issues,
        "corpus_issues": corpus_issues,
        "stats_issues": stats_issues,
    }})
print(json.dumps({{"summary": summary, "segments": segments}}, ensure_ascii=False))
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


def main() -> int:
    log(f"soak-timeline episodes={EPISODES} durations={DURATIONS_CSV}")
    clear_segments_c = None
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=30)
    clear_segments(c)

    env = os.environ.copy()
    env.update(
        {
            "RC_CAPTURE_PASS": PASS,
            "EPISODES": str(EPISODES),
            "DURATIONS_CSV": DURATIONS_CSV,
            "MIN_SEC": "30",
            "MAX_SEC": "120",
            "POST_READY_SEC": "5",
            "WAIT_AFTER_STOP": os.environ.get("E2E_WAIT_AFTER_STOP", "22"),
            "INTER_EPISODE_COOLDOWN_SEC": "15",
            "SEGMENT_FINALIZE_TIMEOUT": "180",
            "EGO_E2E_RECORD_ONLY": "1",
            "EGO_UPLOAD_UNTIL_COMPLETE": "0",
            "RUN_LOG": str(RUN_LOG),
        }
    )
    proc = subprocess.run(
        [sys.executable, "-u", str(SCRIPT_DIR / "ego-e2e-20seg-record-upload.py")],
        env=env,
        cwd=str(DATALAB),
    )
    if proc.returncode != 0:
        log(f"FAIL record phase exit={proc.returncode}")
        return proc.returncode

    rc, out = ssh_run(
        c,
        f"ls -1dt {SEG}/sessions/sess_* 2>/dev/null | xargs -n1 basename",
        timeout=30,
    )
    sessions = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("sess_")]
    episodes: list[dict] = []
    corpus_fail = 0
    stats_fail = 0
    seg_total = 0
    for sid in sessions:
        detail = timeline_tier_report(c, sid)
        for seg in detail.get("segments") or []:
            seg_total += 1
            if not seg.get("corpus_ok"):
                corpus_fail += 1
            if not seg.get("stats_ok"):
                stats_fail += 1
        episodes.append({"session_id": sid, **detail})

    report = {
        "stamp": STAMP,
        "station": STATION,
        "episodes_requested": EPISODES,
        "sessions_recorded": len(sessions),
        "segments_total": seg_total,
        "corpus_fail_segments": corpus_fail,
        "stats_fail_segments": stats_fail,
        "corpus_fail_rate": round(corpus_fail / seg_total, 4) if seg_total else None,
        "stats_fail_rate": round(stats_fail / seg_total, 4) if seg_total else None,
        "tiers": {
            "corpus": "EGO_TIMELINE_MAX_DEV=0.03 (upload/corpus QC)",
            "stats": "EGO_TIMELINE_STATS_MAX_DEV=0.05 (soak reporting only)",
        },
        "episodes": episodes,
        "log": str(RUN_LOG),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"soak-timeline-{STAMP}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    log(
        f"summary segments={seg_total} corpus_fail={corpus_fail} "
        f"stats_fail={stats_fail} report={out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
