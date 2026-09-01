#!/usr/bin/env python3
"""Daily pilot metrics for ego-001: G1-G3 gates + O1/O2 observe fields."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _read_json(path: Path, default: object = None) -> object:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def stream_root(datalab_root: Path, station: str) -> Path:
    return datalab_root / "data-storage" / "stream" / station


def list_recent_sessions(stream_root_dir: Path, *, days: int = 7) -> list[str]:
    sessions_dir = stream_root_dir / "state" / "sessions"
    if not sessions_dir.is_dir():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    out: list[tuple[float, str]] = []
    for sess_dir in sessions_dir.iterdir():
        if not sess_dir.is_dir() or not sess_dir.name.startswith("sess_"):
            continue
        latest = 0.0
        for marker in sess_dir.glob("session.*"):
            try:
                latest = max(latest, marker.stat().st_mtime)
            except OSError:
                pass
        if latest >= cutoff:
            out.append((latest, sess_dir.name))
    out.sort(reverse=True)
    return [sid for _, sid in out]


def validate_mcap_segment(archive: Path) -> dict:
    script = Path(__file__).resolve().parents[1] / "lerobot-studio" / "scripts" / "validate-mcap-archive.py"
    if not script.is_file() or not archive.is_file():
        return {"ok": None, "skipped": True}
    proc = subprocess.run(
        [sys.executable, str(script), str(archive)],
        capture_output=True,
        text=True,
        check=False,
    )
    raw = (proc.stdout or proc.stderr or "").strip().splitlines()
    if not raw:
        return {"ok": False, "error": "no_output"}
    try:
        return json.loads(raw[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": raw[-1][:200]}


def collect_session_metrics(stream_root_dir: Path, session_id: str) -> dict:
    sess_dir = stream_root_dir / "state" / "sessions" / session_id
    failed = (sess_dir / "session.FAILED").is_file()
    ready = (sess_dir / "session.READY").is_file()
    done_upload = (sess_dir / "session.DONE_UPLOAD").is_file()

    unit_path = stream_root_dir / "derived" / session_id / "unit.json"
    unit = _read_json(unit_path, {}) if unit_path.is_file() else {}
    derive = unit.get("derive") if isinstance(unit, dict) else {}
    derive = derive if isinstance(derive, dict) else {}

    o1_s = round(float(derive.get("elapsed_ms") or 0) / 1000.0, 2)
    frames = int(unit.get("frames") or derive.get("frames") or 0) if isinstance(unit, dict) else 0

    mcap_ok = True
    mcap_checked = 0
    raw_dir = stream_root_dir / "raw" / "segments" / session_id
    if raw_dir.is_dir():
        for archive in sorted(raw_dir.glob("*.mcap.zst")):
            result = validate_mcap_segment(archive)
            if result.get("skipped"):
                continue
            mcap_checked += 1
            if not result.get("ok"):
                mcap_ok = False

    g1 = not failed
    g2 = mcap_ok if mcap_checked else True
    g3 = ready if done_upload else None

    return {
        "session_id": session_id,
        "frames": frames,
        "G1_no_failed": g1,
        "G2_mcap_valid": g2,
        "G3_ready_after_upload": g3,
        "derive_total_s": o1_s if ready else None,
        "mux_mode": derive.get("mux_mode"),
        "reconcile_warning": derive.get("reconcile_warning"),
        "failed_reason": _read_json(sess_dir / "session.FAILED", {}).get("reason")
        if failed
        else None,
    }


def summarize(rows: list[dict]) -> dict:
    uploaded = [r for r in rows if r.get("G3_ready_after_upload") is not None]
    failed = [r for r in rows if not r.get("G1_no_failed")]
    ready = [r for r in uploaded if r.get("G3_ready_after_upload")]
    derive_times = [r["derive_total_s"] for r in ready if isinstance(r.get("derive_total_s"), (int, float))]
    derive_times.sort()
    p95 = None
    if derive_times:
        idx = max(0, int(len(derive_times) * 0.95) - 1)
        p95 = derive_times[idx]
    return {
        "sessions_scanned": len(rows),
        "uploaded_sessions": len(uploaded),
        "failed_count": len(failed),
        "ready_count": len(ready),
        "G1_failed_rate": round(len(failed) / len(rows), 4) if rows else 0.0,
        "G3_ready_rate": round(len(ready) / len(uploaded), 4) if uploaded else None,
        "O1_derive_total_s_p95": p95,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect ego pilot daily metrics (G1-G3, O1)")
    parser.add_argument("--station", default="ego-001")
    parser.add_argument("--datalab-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(args.datalab_root)
    sroot = stream_root(root, args.station)
    sessions = list_recent_sessions(sroot, days=args.days)
    rows = [collect_session_metrics(sroot, sid) for sid in sessions]
    summary = summarize(rows)
    payload = {
        "station": args.station,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "sessions": rows,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"station={args.station} sessions={summary['sessions_scanned']} failed={summary['failed_count']}")
        print(
            f"G1_failed_rate={summary['G1_failed_rate']} "
            f"G3_ready_rate={summary['G3_ready_rate']} "
            f"O1_p95_s={summary['O1_derive_total_s_p95']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
