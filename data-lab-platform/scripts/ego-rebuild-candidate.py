#!/usr/bin/env python3
"""Rebuild an isolated candidate LeRobot dataset (does not touch corpus or releases).

Fixes episode-local timestamp semantics in a staging copy, then optionally runs
the official loader regression gate.

Usage:
  python ego-rebuild-candidate.py \\
      --source data-storage/corpus/ego_001 \\
      --dest data-storage/ego-delivery/candidates/ego-001-m1-staging \\
      --fix-timestamps \\
      --run-loader-regression
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_fps(root: Path) -> float:
    info = json.loads((root / "meta" / "info.json").read_text(encoding="utf-8"))
    return float(info.get("fps") or 30.0)


def fix_timestamps_in_place(dest: Path) -> dict:
    fps = _load_fps(dest)
    fixed_files = 0
    fixed_rows = 0
    for pq_path in sorted((dest / "data").rglob("*.parquet")):
        table = pq.read_table(pq_path)
        if "timestamp" not in table.column_names or "frame_index" not in table.column_names:
            continue
        local = [int(x or 0) for x in table["frame_index"].to_pylist()]
        new_ts = pa.array([float(i) / fps for i in local], type=pa.float32())
        cols = {name: table[name] for name in table.column_names}
        cols["timestamp"] = new_ts
        out = pa.table(cols)
        tmp = pq_path.with_suffix(".parquet.tmp")
        pq.write_table(out, tmp)
        tmp.replace(pq_path)
        fixed_files += 1
        fixed_rows += out.num_rows
    return {"fixed_files": fixed_files, "fixed_rows": fixed_rows, "fps": fps}


def copy_dataset_tree(source: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    for top in ("meta", "data", "videos", "sensor_raw", "offline"):
        src = source / top
        if src.is_dir():
            shutil.copytree(src, dest / top)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--fix-timestamps", action="store_true")
    parser.add_argument("--run-loader-regression", action="store_true")
    parser.add_argument("--lerobot-repo", type=Path, default=None)
    parser.add_argument("--sample", choices=("head_mid_tail", "all"), default="all")
    args = parser.parse_args(argv)

    source = args.source.resolve()
    dest = args.dest.resolve()
    if not (source / "meta" / "info.json").is_file():
        print(f"missing source dataset: {source}", file=sys.stderr)
        return 1

    copy_dataset_tree(source, dest)
    report: dict = {
        "generated_at": _utc_now(),
        "tier": "candidate",
        "source": str(source),
        "dest": str(dest),
        "fixes": {},
    }

    if args.fix_timestamps:
        report["fixes"]["timestamps"] = fix_timestamps_in_place(dest)

    manifest_path = dest / "meta" / "candidate_manifest.json"
    manifest_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.run_loader_regression:
        here = Path(__file__).resolve().parent
        lerobot_repo = args.lerobot_repo or (here.parents[2] / "lerobot")
        gate_script = here / "ego-lerobot-loader-gate.py"
        reg_report = dest / "meta" / "loader_regression.json"
        cmd = [
            sys.executable,
            str(gate_script),
            str(dest),
            "--lerobot-repo",
            str(lerobot_repo),
            "--video-backends",
            "pyav",
            "torchcodec",
            "--sample",
            args.sample,
            "--dataloader",
            "--report",
            str(reg_report),
        ]
        proc = subprocess.run(cmd, check=False)
        report["loader_regression_exit_code"] = proc.returncode
        manifest_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return proc.returncode

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
