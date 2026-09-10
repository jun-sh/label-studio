#!/usr/bin/env python3
"""Offline official LeRobot v3 loader gate (LeRobotDataset regression guard).

Official v3 canonical task metadata: ``meta/tasks.parquet`` only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap_ego_platform(datalab_root: Path) -> None:
    candidates = [
        datalab_root.parent / "ego-platform" / "src",
        Path("/ego-platform/src"),
    ]
    for candidate in candidates:
        if (candidate / "ego_platform").is_dir():
            sys.path.insert(0, str(candidate))
            return


def validate_dataset_root(root: Path) -> dict:
    root = root.resolve()
    errors: list[str] = []
    info_path = root / "meta" / "info.json"
    if not info_path.is_file():
        return {"ok": False, "errors": [f"missing meta/info.json: {root}"]}

    info = json.loads(info_path.read_text(encoding="utf-8"))
    codebase_version = str(info.get("codebase_version") or "")
    if codebase_version not in ("v3.0", "v3.1"):
        errors.append(f"unexpected codebase_version: {codebase_version or '(empty)'}")

    tasks_parquet = root / "meta" / "tasks.parquet"
    if not tasks_parquet.is_file():
        errors.append("missing meta/tasks.parquet (official v3 canonical tasks metadata)")

    try:
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        from lerobot.datasets.utils import DEFAULT_TASKS_PATH
    except ImportError as exc:
        return {
            "ok": False,
            "errors": [f"lerobot package unavailable: {exc}"],
        }

    if tasks_parquet.is_file() and DEFAULT_TASKS_PATH != "meta/tasks.parquet":
        errors.append(f"unexpected DEFAULT_TASKS_PATH: {DEFAULT_TASKS_PATH}")

    try:
        ds = LeRobotDataset(repo_id=root.name, root=root)
        _ = len(ds)
        if hasattr(ds, "meta") and ds.meta is not None:
            _ = ds.meta.total_episodes
            tasks = getattr(ds.meta, "tasks", None)
            if tasks is None:
                errors.append("LeRobotDataset.meta.tasks missing after load")
    except Exception as exc:
        errors.append(f"LeRobotDataset load failed: {exc}")

    return {"ok": not errors, "errors": errors, "root": str(root)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", help="Dataset root directories to validate")
    parser.add_argument(
        "--datalab-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="data-lab-platform root (for ego-platform PYTHONPATH bootstrap)",
    )
    args = parser.parse_args(argv)
    _bootstrap_ego_platform(args.datalab_root)

    failed = 0
    for raw in args.roots:
        result = validate_dataset_root(Path(raw))
        status = "OK" if result["ok"] else "FAIL"
        print(f"[{status}] {result.get('root', raw)}")
        for err in result.get("errors") or []:
            print(f"  - {err}")
        if not result["ok"]:
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
