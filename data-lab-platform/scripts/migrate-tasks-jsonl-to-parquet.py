#!/usr/bin/env python3
"""One-shot migrate legacy meta/tasks.jsonl to official meta/tasks.parquet."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _ensure_ego_platform_path(datalab_root: Path) -> None:
    for candidate in (
        datalab_root.parent / "ego-platform" / "src",
        Path("/ego-platform/src"),
    ):
        if (candidate / "ego_platform").is_dir():
            sys.path.insert(0, str(candidate))
            return


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(
                {
                    "task_index": int(payload.get("task_index", len(rows))),
                    "task": str(payload.get("task", "")),
                }
            )
    return rows


def migrate_root(root: Path, *, remove_jsonl: bool) -> str:
    root = root.resolve()
    meta = root / "meta"
    jsonl = meta / "tasks.jsonl"
    parquet = meta / "tasks.parquet"

    if parquet.is_file():
        if remove_jsonl and jsonl.is_file():
            jsonl.unlink()
            return "removed-legacy-jsonl"
        return "skip-parquet-exists"

    if not jsonl.is_file():
        return "skip-no-jsonl"

    rows = _read_jsonl(jsonl)
    if not rows:
        return "skip-empty-jsonl"

    try:
        from ego_platform.lerobot.io import write_tasks_parquet
    except ImportError as exc:
        raise RuntimeError(
            "ego_platform required to migrate tasks.jsonl to canonical tasks.parquet"
        ) from exc

    write_tasks_parquet(root, rows)
    if remove_jsonl and jsonl.is_file():
        jsonl.unlink()
    return "migrated"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path, help="Dataset roots to migrate")
    parser.add_argument(
        "--remove-jsonl",
        action="store_true",
        help="Delete meta/tasks.jsonl after successful migration",
    )
    parser.add_argument(
        "--datalab-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args(argv)
    _ensure_ego_platform_path(args.datalab_root)

    for raw in args.roots:
        status = migrate_root(raw, remove_jsonl=args.remove_jsonl)
        print(f"{raw.resolve()}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
