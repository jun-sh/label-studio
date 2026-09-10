#!/usr/bin/env python3
"""Repair ego data parquet columns to match official LeRobot v3 loader expectations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq


def _bootstrap_ego_platform(datalab_root: Path) -> None:
    for candidate in (
        datalab_root.parent / "ego-platform" / "src",
        Path("/ego-platform/src"),
    ):
        if (candidate / "ego_platform").is_dir():
            sys.path.insert(0, str(candidate))
            return


def repair_dataset_root(root: Path, *, dry_run: bool = False) -> str:
    root = root.resolve()
    info_path = root / "meta" / "info.json"
    if not info_path.is_file():
        return "skip-no-info"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    features = info.get("features") or {}
    data_dir = root / "data" / "chunk-000"
    if not data_dir.is_dir():
        return "skip-no-data"
    paths = sorted(data_dir.glob("file-*.parquet"))
    if not paths:
        return "skip-no-parquet"

    from ego_platform.lerobot.data_schema import cast_data_table_to_info, cast_episodes_table

    changed = 0
    ep_dir = root / "meta" / "episodes" / "chunk-000"
    if ep_dir.is_dir():
        for path in sorted(ep_dir.glob("file-*.parquet")):
            table = pq.read_table(path)
            fixed = cast_episodes_table(table)
            if fixed.schema.equals(table.schema):
                continue
            changed += 1
            if dry_run:
                print(f"would repair episodes {path}")
                continue
            tmp = path.with_suffix(path.suffix + ".tmp")
            pq.write_table(fixed, tmp)
            tmp.replace(path)

    for path in paths:
        table = pq.read_table(path)
        fixed = cast_data_table_to_info(table, features)
        if fixed.schema.equals(table.schema):
            continue
        changed += 1
        if dry_run:
            print(f"would repair {path}")
            print(f"  before: {table.schema}")
            print(f"  after:  {fixed.schema}")
            continue
        tmp = path.with_suffix(path.suffix + ".tmp")
        pq.write_table(fixed, tmp)
        tmp.replace(path)
    if changed == 0:
        return "skip-already-valid"
    return f"repaired-{changed}" if not dry_run else f"dry-run-{changed}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", help="Dataset roots (corpus or stream station)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--datalab-root",
        type=Path,
        default=None,
        help="data-lab-platform root (for ego-platform PYTHONPATH bootstrap)",
    )
    args = parser.parse_args(argv)
    if args.datalab_root is None:
        here = Path(__file__).resolve()
        args.datalab_root = next(
            (parent for parent in here.parents if parent.name == "data-lab-platform"),
            here.parent if here.parent.name == "scripts" else Path("/app"),
        )
    _bootstrap_ego_platform(args.datalab_root)

    failed = 0
    for raw in args.roots:
        try:
            status = repair_dataset_root(Path(raw), dry_run=args.dry_run)
            print(f"[{status}] {Path(raw).resolve()}")
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {raw}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
