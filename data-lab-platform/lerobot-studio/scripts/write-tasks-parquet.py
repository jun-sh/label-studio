#!/usr/bin/env python3
"""Write LeRobot v3 meta/tasks.parquet (official task metadata)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def _ensure_ego_platform_path() -> None:
    here = Path(__file__).resolve()
    candidates = [Path("/ego-platform/src")]
    for parent in here.parents:
        candidates.append(parent / "ego-platform" / "src")
    for candidate in candidates:
        if (candidate / "ego_platform").is_dir():
            sys.path.insert(0, str(candidate))
            return


def _atomic_parquet_write(table: pa.Table, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    pq.write_table(table, tmp)
    tmp.replace(out)


def write_tasks_parquet(root: Path, rows: list[dict]) -> None:
    _ensure_ego_platform_path()
    try:
        from ego_platform.lerobot.io import write_tasks_parquet as ego_write_tasks_parquet

        ego_write_tasks_parquet(root, rows)
        return
    except ImportError:
        pass

    import pandas as pd

    meta = root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    normalized = [
        {"task_index": int(row.get("task_index", idx)), "task": str(row.get("task", ""))}
        for idx, row in enumerate(rows)
    ]
    if not normalized:
        normalized = [{"task_index": 0, "task": ""}]
    tasks = [row["task"] for row in normalized]
    task_indices = [row["task_index"] for row in normalized]
    df = pd.DataFrame({"task_index": task_indices}, index=pd.Index(tasks, name="task"))
    _atomic_parquet_write(pa.Table.from_pandas(df, preserve_index=True), meta / "tasks.parquet")
    legacy = meta / "tasks.jsonl"
    if legacy.is_file():
        legacy.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Dataset / station root")
    parser.add_argument(
        "--rows",
        type=Path,
        required=True,
        help="JSON file with [{task_index, task}, ...]",
    )
    args = parser.parse_args(argv)
    rows = json.loads(args.rows.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print("--rows must contain a JSON array", file=sys.stderr)
        return 2
    write_tasks_parquet(args.root.resolve(), rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
