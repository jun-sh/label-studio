#!/usr/bin/env python3
"""Analyze round5 strict 20Hz soak log and segment batch validation."""

from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
from datetime import datetime
from pathlib import Path

SOAK_WARMUP_S = max(0, int(os.environ.get("EGO_SOAK_WARMUP_S", "180")))


def parse_soak_ts(line: str, marker: str) -> datetime | None:
    if marker not in line:
        return None
    part = line.split(marker, 1)[-1].strip()
    part = part.split("pid=")[0].strip().rstrip("=").strip()
    try:
        return datetime.fromisoformat(part)
    except ValueError:
        return None


log_path = Path("/tmp/strict20hz_soak_round5.log")
text = log_path.read_text(errors="ignore") if log_path.is_file() else ""
soak_start = soak_end = None
fps_out_of_band = 0
for line in text.splitlines():
    if "round5 soak start" in line:
        soak_start = parse_soak_ts(line, "start")
    if "round5 soak end" in line:
        soak_end = parse_soak_ts(line, "end") or parse_soak_ts(
            line.split("fps_out_of_band=", 1)[0], "end"
        )
        m = re.search(r"fps_out_of_band=(\d+)", line)
        if m:
            fps_out_of_band = int(m.group(1))

def _journal_line_ts(line: str) -> datetime | None:
    m = re.match(r"(\w{3}) (\d{1,2}) (\d{2}:\d{2}:\d{2})", line)
    if not m or soak_start is None:
        return None
    try:
        return datetime.strptime(
            f"{soak_start.year} {m.group(1)} {m.group(2)} {m.group(3)}",
            "%Y %b %d %H:%M:%S",
        ).replace(tzinfo=soak_start.tzinfo)
    except ValueError:
        return None


warmup_end = (
    soak_start.timestamp() + SOAK_WARMUP_S if soak_start is not None else None
)

fps_vals: list[float] = []
dropped: list[int] = []
pq: list[int] = []
pending: list[int] = []
pending_high = 0
for line in text.splitlines():
    m = re.search(
        r"capture_fps=([\d.]+).*pending_segments=(\d+).*persist_q=(\d+).*dropped=(\d+)",
        line,
    )
    if m:
        line_ts = _journal_line_ts(line)
        if warmup_end is not None and line_ts is not None:
            if line_ts.timestamp() < warmup_end:
                continue
        fps_vals.append(float(m.group(1)))
        pending.append(int(m.group(2)))
        pq.append(int(m.group(3)))
        dropped.append(int(m.group(4)))
    if line.startswith("PENDING_HIGH"):
        if warmup_end is not None:
            m2 = re.search(r"ts=([0-9T:+-]+)", line)
            if m2:
                try:
                    if datetime.fromisoformat(m2.group(1)).timestamp() < warmup_end:
                        continue
                except ValueError:
                    pass
        pending_high += 1

sessions_root = Path("/home/server/cache/ego-lan-214/segments/sessions")
sess_dirs = sorted(
    [p for p in sessions_root.iterdir() if p.is_dir() and p.name.startswith("sess_")],
    key=lambda p: p.stat().st_mtime,
    reverse=True,
)
base = (sess_dirs[0] / "segments") if sess_dirs else None
session_id = sess_dirs[0].name if sess_dirs else None

window: list[dict] = []
def _manifest_created_ts(seg_dir: Path) -> float | None:
    manifest_path = seg_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        created = str(m.get("created_at") or "").strip()
        if created.endswith("Z"):
            created = created[:-1] + "+00:00"
        return datetime.fromisoformat(created).timestamp()
    except (OSError, json.JSONDecodeError, ValueError):
        return seg_dir.stat().st_mtime


val_log = Path("/tmp/strict20hz_segment_validation.jsonl")
if val_log.is_file() and soak_start and soak_end:
    t0 = soak_start.timestamp() + SOAK_WARMUP_S
    t1 = soak_end.timestamp()
    by_seg: dict[str, dict] = {}
    for line in val_log.read_text(errors="ignore").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        validated_at = r.get("validated_at", "")
        try:
            vt = datetime.fromisoformat(validated_at).timestamp()
        except (ValueError, TypeError):
            continue
        if t0 <= vt <= t1 + 120:
            by_seg[str(r.get("seg") or "")] = r
    window.extend(by_seg.values())
elif base and soak_start and soak_end:
    t0 = soak_start.timestamp() + SOAK_WARMUP_S
    t1 = soak_end.timestamp()
    py_bin = "/home/server/workspace/ego-studio/.venv/bin/python3"
    val = "/home/server/workspace/ego-studio/src/ego_capture_studio/tools/validate_strict_20hz.py"
    row_paths: list[Path] = list(base.glob("seg_*/rows.jsonl"))
    archive_base = Path("/tmp/strict20hz_soak_archive") / (session_id or "") / "segments"
    if archive_base.is_dir():
        row_paths.extend(archive_base.glob("seg_*/rows.jsonl"))
    seen: set[str] = set()
    for p in sorted(row_paths):
        if p.parent.name in seen:
            continue
        created_ts = _manifest_created_ts(p.parent)
        if created_ts is None or not (t0 <= created_ts <= t1 + 120):
            continue
        seen.add(p.parent.name)
        proc = subprocess.run([py_bin, val, str(p)], capture_output=True, text=True)
        try:
            r = json.loads(proc.stdout)
        except json.JSONDecodeError:
            continue
        r["seg"] = p.parent.name
        r["ok"] = proc.returncode == 0
        window.append(r)

good = [s for s in window if s.get("ok")]
bad = [s for s in window if not s.get("ok")]
huge = [s for s in window if (s.get("cam_offset_max_ms") or 0) > 100]

fps_in_band = (
    all(19.9 <= f <= 20.1 for f in fps_vals) if fps_vals else False
)
pending_ok = (max(pending) if pending else 0) < 30

journal_fps: list[float] = []
if soak_start and soak_end:
    since_dt = soak_start
    if SOAK_WARMUP_S > 0:
        from datetime import timedelta

        since_dt = soak_start + timedelta(seconds=SOAK_WARMUP_S)
    since = since_dt.strftime("%Y-%m-%d %H:%M:%S")
    until = soak_end.strftime("%Y-%m-%d %H:%M:%S")
    proc = subprocess.run(
        [
            "journalctl",
            "--user",
            "-u",
            "ecs-record-oak-stream",
            "--since",
            since,
            "--until",
            until,
            "--no-pager",
        ],
        capture_output=True,
        text=True,
        errors="ignore",
    )
    for line in (proc.stdout or "").splitlines():
        m = re.search(r"capture_fps=([\d.]+)", line)
        if m:
            journal_fps.append(float(m.group(1)))

report = {
    "session": session_id,
    "soak_start": soak_start.isoformat() if soak_start else None,
    "soak_end": soak_end.isoformat() if soak_end else None,
    "soak_warmup_s": SOAK_WARMUP_S,
    "segments_total": len(window),
    "segments_pass": len(good),
    "pass_rate_pct": round(100.0 * len(good) / len(window), 2) if window else 0.0,
    "fail_segments": [r["seg"] for r in bad[:25]],
    "huge_offset_segs": [r["seg"] for r in huge],
    "capture_fps_mean": round(statistics.mean(fps_vals), 3) if fps_vals else None,
    "capture_fps_min": round(min(fps_vals), 3) if fps_vals else None,
    "capture_fps_max": round(max(fps_vals), 3) if fps_vals else None,
    "capture_fps_in_band_19_9_20_1": fps_in_band,
    "journal_fps_samples": len(journal_fps),
    "journal_fps_in_band_19_9_20_1": (
        all(19.9 <= f <= 20.1 for f in journal_fps) if journal_fps else False
    ),
    "journal_fps_min": round(min(journal_fps), 3) if journal_fps else None,
    "journal_fps_max": round(max(journal_fps), 3) if journal_fps else None,
    "fps_out_of_band_samples": fps_out_of_band,
    "pending_max": max(pending) if pending else None,
    "pending_high_events": pending_high,
    "pending_ok_below_30": pending_ok,
    "dropped_max": max(dropped) if dropped else None,
    "persist_q_max": max(pq) if pq else None,
    "go_live": bool(
        window
        and len(good) == len(window)
        and not huge
        and fps_in_band
        and fps_out_of_band == 0
        and pending_ok
        and (max(pq) if pq else 99) <= 1
        and (max(dropped) if dropped else 99) == 0
        and (
            all(19.9 <= f <= 20.1 for f in journal_fps) if journal_fps else False
        )
    ),
}
out = Path("/tmp/strict20hz_round5_signoff.json")
out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2))
