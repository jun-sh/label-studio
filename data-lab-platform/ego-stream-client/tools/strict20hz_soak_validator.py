#!/usr/bin/env python3
"""Validate each segment at close time before upload dequeue deletes it."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

SEGMENT_ROOT = Path(os.environ.get("EGO_SEGMENT_ROOT", "/home/server/cache/ego-lan-214/segments"))
OUT = Path(os.environ.get("EGO_SOAK_VALIDATION_LOG", "/tmp/strict20hz_segment_validation.jsonl"))
PY = os.environ.get("EGO_PYTHON", "/home/server/workspace/ego-studio/.venv/bin/python3")
VAL = os.environ.get(
    "EGO_VALIDATE_SCRIPT",
    "/home/server/workspace/ego-studio/src/ego_capture_studio/tools/validate_strict_20hz.py",
)
POLL_S = float(os.environ.get("EGO_SOAK_VALIDATOR_POLL_S", "2"))


def _resolve_session() -> str:
    reg_path = SEGMENT_ROOT / "registry.json"
    if reg_path.is_file():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        sessions = {
            sid: meta
            for sid, meta in (reg.get("sessions") or {}).items()
            if sid and str(sid).startswith("sess_")
        }
        if sessions:
            return sorted(sessions.keys(), key=lambda s: sessions[s]["updatedAt"])[-1]
    subs = sorted(
        p.name for p in (SEGMENT_ROOT / "sessions").iterdir() if p.is_dir() and p.name.startswith("sess_")
    )
    return subs[-1] if subs else ""


def _closed_segments(session_id: str) -> list[Path]:
    seg_root = SEGMENT_ROOT / "sessions" / session_id / "segments"
    if not seg_root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(seg_root.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / "manifest.json"
        rows = child / "rows.jsonl"
        if not manifest.is_file() or not rows.is_file():
            continue
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if m.get("closed"):
            out.append(child)
    return out


def main() -> None:
    soak_start = datetime.now().astimezone()
    print(f"validator_start {soak_start.isoformat()}", flush=True)
    seen: set[str] = set()
    if OUT.is_file():
        for line in OUT.read_text(errors="ignore").splitlines():
            try:
                seen.add(json.loads(line)["seg"])
            except (json.JSONDecodeError, KeyError):
                pass
    while True:
        session_id = _resolve_session()
        if not session_id:
            time.sleep(POLL_S)
            continue
        for seg_dir in _closed_segments(session_id):
            name = seg_dir.name
            if name in seen:
                continue
            rows = seg_dir / "rows.jsonl"
            proc = subprocess.run([PY, VAL, str(rows)], capture_output=True, text=True)
            try:
                report = json.loads(proc.stdout)
            except json.JSONDecodeError:
                continue
            report["seg"] = name
            report["ok"] = proc.returncode == 0
            report["validated_at"] = datetime.now().astimezone().isoformat()
            OUT.open("a", encoding="utf-8").write(json.dumps(report, separators=(",", ":")) + "\n")
            seen.add(name)
            print(
                f"validated {name} ok={report['ok']} eff_hz={report.get('eff_hz')} "
                f"pct={report.get('pct_49_51')}",
                flush=True,
            )
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
