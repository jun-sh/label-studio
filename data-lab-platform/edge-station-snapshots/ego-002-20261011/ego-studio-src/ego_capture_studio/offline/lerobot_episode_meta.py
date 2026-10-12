"""Read LeRobot v3 episode metadata from local parquet without importing torch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_dataset_info(dataset_root: Path) -> dict[str, Any]:
    path = dataset_root / "meta" / "info.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def find_episode_metadata_row(dataset_root: Path, episode_index: int) -> dict[str, Any]:
    """Return one row from meta/episodes/**/*.parquet as plain Python scalars."""
    try:
        import pyarrow.parquet as pq
    except ImportError as e:
        raise RuntimeError("pyarrow is required for episode metadata lookup") from e

    episodes_root = dataset_root / "meta" / "episodes"
    if not episodes_root.is_dir():
        raise FileNotFoundError(episodes_root)

    for path in sorted(episodes_root.rglob("*.parquet")):
        table = pq.read_table(path, filters=[("episode_index", "=", episode_index)])
        if table.num_rows == 0:
            continue
        return {name: table[name][0].as_py() for name in table.column_names}

    raise KeyError(f"episode_index {episode_index} not found under {episodes_root}")


def resolve_episode_video_path(
    dataset_root: Path,
    info: dict[str, Any],
    episode_row: dict[str, Any],
    video_key: str,
) -> Path:
    pattern = info.get("video_path")
    if not pattern:
        raise KeyError("info.json missing video_path")
    chunk = episode_row[f"videos/{video_key}/chunk_index"]
    file_idx = episode_row[f"videos/{video_key}/file_index"]
    rel = pattern.format(video_key=video_key, chunk_index=chunk, file_index=file_idx)
    return (dataset_root / rel).resolve()
