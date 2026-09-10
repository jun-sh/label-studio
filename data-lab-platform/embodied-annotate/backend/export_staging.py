"""Stage LeRobot meta files for training export (export contract v1.0)."""

from __future__ import annotations

import shutil
from pathlib import Path

from export_builders import TRAINING_META_BLOCKLIST

_META_FILES = ("info.json", "stats.json", "tasks.parquet")


def stage_training_meta(src_meta: Path, dst_meta: Path) -> None:
    """Copy native LeRobot meta; exclude annotation JSON and deprecated training files."""
    if dst_meta.exists():
        shutil.rmtree(dst_meta)
    dst_meta.mkdir(parents=True, exist_ok=True)

    for name in _META_FILES:
        src = src_meta / name
        if src.is_file():
            shutil.copy2(src, dst_meta / name)

    src_episodes = src_meta / "episodes"
    if src_episodes.is_dir():
        shutil.copytree(src_episodes, dst_meta / "episodes")

    for blocked in TRAINING_META_BLOCKLIST:
        blocked_path = dst_meta / blocked
        if blocked_path.exists():
            if blocked_path.is_dir():
                shutil.rmtree(blocked_path)
            else:
                blocked_path.unlink()
