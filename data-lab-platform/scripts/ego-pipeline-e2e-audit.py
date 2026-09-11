#!/usr/bin/env python3
"""Commercial EGO pipeline step audit (130 capture → 34 postprocess)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATION_DEFAULT = "ego-001"
SLUG_DEFAULT = "ego_001"


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _step(steps: list[dict[str, Any]], name: str, ok: bool, detail: str, **extra: Any) -> None:
    steps.append(
        {
            "step": name,
            "ok": ok,
            "detail": detail,
            **extra,
        }
    )
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}: {detail}")


def audit_34_state(datalab: Path, station: str, slug: str, steps: list[dict[str, Any]]) -> None:
    stream = datalab / "data-storage/stream" / station
    pipeline = datalab / "data-storage/pipeline" / station
    corpus = datalab / "data-storage/corpus" / slug

    raw = sorted(p.name for p in (stream / "raw/segments").glob("sess_*") if p.is_dir())
    ready = sorted(
        p.parent.name
        for p in (stream / "state/sessions").glob("sess_*/session.READY")
        if p.is_file()
    )
    done_up = sorted(
        p.parent.name
        for p in (stream / "state/sessions").glob("sess_*/session.DONE_UPLOAD")
        if p.is_file()
    )
    seals = sorted((stream / "live/session_seals").glob("*.json")) if (stream / "live/session_seals").is_dir() else []
    finalize = sorted(
        p.parent.parent.name
        for p in pipeline.glob("sess_*/.status/finalize.done")
        if p.is_file()
    )

    _step(
        steps,
        "S4-ingest-markers",
        len(done_up) >= len(raw) and len(raw) > 0,
        f"raw={len(raw)} DONE_UPLOAD={len(done_up)} session_seals={len(seals)}",
        sessions_raw=raw,
        sessions_done_upload=done_up,
    )
    _step(
        steps,
        "S5-derive-ready",
        len(ready) >= len(raw) and len(raw) > 0,
        f"session.READY={len(ready)}/{len(raw)}",
        sessions_ready=ready,
    )
    _step(
        steps,
        "S6-convert-finalize",
        len(finalize) >= len(ready) if ready else False,
        f"finalize.done={len(finalize)}/{len(ready)}",
        sessions_finalize=finalize,
    )

    info = corpus / "meta/info.json"
    if info.is_file():
        meta = json.loads(info.read_text(encoding="utf-8"))
        eps = int(meta.get("total_episodes") or 0)
        frames = int(meta.get("total_frames") or 0)
        _step(
            steps,
            "S7-corpus-published",
            eps > 0 and frames > 0,
            f"corpus episodes={eps} frames={frames} slug={slug}",
        )
    else:
        _step(steps, "S7-corpus-published", False, f"missing {info}")

    samples = datalab / "data-storage/samples" / f"{slug}.zip"
    _step(steps, "S7-viewer-samples", samples.is_file(), f"samples.zip={'yes' if samples.is_file() else 'no'}")


def audit_session_detail(datalab: Path, station: str, session_id: str, steps: list[dict[str, Any]]) -> None:
    stream = datalab / "data-storage/stream" / station
    seg_root = stream / "raw/segments" / session_id
    segs = sorted(p for p in seg_root.glob("seg_*") if p.is_dir()) if seg_root.is_dir() else []
    corrupt = 0
    mcap = 0
    for seg in segs:
        man = seg / "manifest.json"
        if not man.is_file():
            continue
        try:
            m = json.loads(man.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            corrupt += 1
            continue
        if m.get("status") == "CORRUPT":
            corrupt += 1
        if (seg / "segment.mcap").is_file() or m.get("storage_format") == "mcap":
            mcap += 1
    _step(
        steps,
        f"S2-segments-{session_id[:12]}",
        len(segs) > 0 and corrupt == 0 and mcap == len(segs),
        f"segments={len(segs)} mcap={mcap} corrupt={corrupt}",
    )

    seal_path = stream / "live/session_seals" / f"{session_id}.json"
    if seal_path.is_file():
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        _step(
            steps,
            f"S3-session-seal-{session_id[:12]}",
            bool(seal.get("complete")),
            f"complete={seal.get('complete')} segment_count={seal.get('segment_count')}",
        )
    else:
        _step(
            steps,
            f"S3-session-seal-{session_id[:12]}",
            True,
            "no server seal (legacy path OK if DONE_UPLOAD set)",
            optional=True,
        )

    ready = (stream / "state/sessions" / session_id / "session.READY").is_file()
    done = (stream / "state/sessions" / session_id / "session.DONE_UPLOAD").is_file()
    _step(
        steps,
        f"S5-session-ready-{session_id[:12]}",
        ready and done,
        f"READY={ready} DONE_UPLOAD={done}",
    )


def run_loader_gate(datalab: Path, corpus: Path, steps: list[dict[str, Any]]) -> None:
    gate = datalab / "data-lab-platform/scripts/ego-lerobot-loader-gate.py"
    lerobot = datalab.parent / "lerobot"
    if not gate.is_file() or not corpus.is_dir():
        _step(steps, "S8-loader-gate", False, "missing gate script or corpus")
        return
    py = lerobot / ".venv/bin/python3"
    if not py.is_file():
        py = Path(sys.executable)
    proc = subprocess.run(
        [
            str(py),
            str(gate),
            str(corpus),
            "--datalab-root",
            str(datalab / "data-lab-platform"),
            "--lerobot-repo",
            str(lerobot),
            "--sample",
            "head_mid_tail",
        ],
        capture_output=True,
        text=True,
    )
    _step(
        steps,
        "S8-loader-gate-corpus",
        proc.returncode == 0,
        (proc.stdout or proc.stderr or "").strip().splitlines()[-1] if proc.stdout or proc.stderr else "no output",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datalab-root", type=Path, default=None)
    parser.add_argument("--station", default=STATION_DEFAULT)
    parser.add_argument("--slug", default=SLUG_DEFAULT)
    parser.add_argument("--session-id", action="append", default=[], help="Focus session(s) from latest run")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()

    script = Path(__file__).resolve()
    datalab = args.datalab_root or script.parents[2]
    steps: list[dict[str, Any]] = []

    print(f"=== EGO pipeline audit @ {_utc()} station={args.station} ===")
    audit_34_state(datalab, args.station, args.slug, steps)
    for sid in args.session_id:
        audit_session_detail(datalab, args.station, sid, steps)
    run_loader_gate(datalab, datalab / "data-storage/corpus" / args.slug, steps)

    failed = [s for s in steps if not s.get("ok")]
    report = {
        "generated_at": _utc(),
        "station": args.station,
        "slug": args.slug,
        "session_ids": args.session_id,
        "steps": steps,
        "ok": len(failed) == 0,
        "failed_count": len(failed),
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"report: {args.report}")

    print(f"=== summary: {len(steps) - len(failed)}/{len(steps)} PASS ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
