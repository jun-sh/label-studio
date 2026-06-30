#!/usr/bin/env python3
"""Rebuild v3.0 meta/data parquet artifacts from live stream jsonl (viewer compatibility)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

VIDEO_KEYS = [
    "observation.images.camera_front_left",
    "observation.images.camera_front_right",
    "observation.images.camera_rear_left",
    "observation.images.camera_rear_right",
]

DEFAULT_TASK = "Perform egocentric manipulation tasks at the laboratory workbench"


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return default


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def write_tasks_jsonl(root: Path, task: str) -> None:
    meta = root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"task_index": 0, "task": task}, ensure_ascii=False)
    (meta / "tasks.jsonl").write_text(line + "\n", encoding="utf-8")


def _atomic_parquet_write(table: pa.Table, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    pq.write_table(table, tmp)
    tmp.replace(out)


def load_episodes_index(root: Path) -> list[dict]:
    raw = read_json(root / "live" / "episodes-index.json", {})
    episodes = raw.get("episodes") if isinstance(raw, dict) else None
    if not isinstance(episodes, list) or not episodes:
        return []
    return sorted(episodes, key=lambda ep: int(ep.get("episode_index", 0)))


def episode_index_for_frame(episodes: list[dict], frame_index: int) -> int:
    for ep in episodes:
        start = int(ep.get("dataset_from_index", 0))
        end = int(ep.get("dataset_to_index", start))
        if start <= frame_index < end:
            return int(ep.get("episode_index", 0))
    return 0


def write_data_parquet(root: Path, rows: list[dict], fps: float, episodes: list[dict]) -> None:
    n = len(rows)
    if n == 0:
        return
    frame_index = [int(r.get("frame_index", i)) for i, r in enumerate(rows)]
    if episodes:
        episode_index = [episode_index_for_frame(episodes, fi) for fi in frame_index]
    else:
        episode_index = [0] * n
    index = frame_index[:]
    task_index = [0] * n
    timestamp = [float(i) / fps for i in frame_index]
    table = pa.table(
        {
            "frame_index": pa.array(frame_index, type=pa.int64()),
            "episode_index": pa.array(episode_index, type=pa.int64()),
            "index": pa.array(index, type=pa.int64()),
            "task_index": pa.array(task_index, type=pa.int64()),
            "timestamp": pa.array(timestamp, type=pa.float64()),
        }
    )
    out = root / "data" / "chunk-000" / "file-000.parquet"
    _atomic_parquet_write(table, out)


def _episode_row(ep: dict, fps: float) -> dict:
    length = int(ep.get("length") or 0)
    from_idx = int(ep.get("dataset_from_index") or 0)
    to_idx = int(ep.get("dataset_to_index") or from_idx + length)
    if length <= 0:
        length = max(0, to_idx - from_idx)
    duration = length / fps if fps > 0 else 0.0
    task = str(ep.get("title") or ep.get("task") or DEFAULT_TASK)
    row: dict = {
        "episode_index": float(ep.get("episode_index", 0)),
        "length": float(length),
        "task_index": 0.0,
        "dataset_from_index": float(from_idx),
        "dataset_to_index": float(to_idx),
        "data/chunk_index": 0.0,
        "data/file_index": 0.0,
        "chunk_index": 0.0,
        "file_index": 0.0,
    }
    for key in VIDEO_KEYS:
        row[f"videos/{key}/chunk_index"] = 0.0
        row[f"videos/{key}/file_index"] = 0.0
        row[f"videos/{key}/from_timestamp"] = float(from_idx) / fps if fps > 0 else 0.0
        row[f"videos/{key}/to_timestamp"] = float(to_idx) / fps if fps > 0 else duration
    row["tasks"] = task
    row["_duration"] = duration
    return row


def write_episodes_parquet(root: Path, episodes: list[dict], fps: float, task: str) -> None:
    if not episodes:
        total_frames = int(read_json(root / "meta" / "info.json", {}).get("total_frames") or 0)
        if total_frames <= 0:
            return
        episodes = [
            {
                "episode_index": 0,
                "length": total_frames,
                "dataset_from_index": 0,
                "dataset_to_index": total_frames,
                "title": task,
            }
        ]

    rows = [_episode_row(ep, fps) for ep in episodes]
    columns: dict[str, pa.Array] = {}
    for key in rows[0]:
        if key == "tasks":
            continue
        if key == "_duration":
            continue
        columns[key] = pa.array([row[key] for row in rows], type=pa.float64())
    columns["tasks"] = pa.array([[row["tasks"]] for row in rows], type=pa.list_(pa.string()))
    table = pa.table(columns)
    out = root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    _atomic_parquet_write(table, out)


def main() -> int:
    argv = [a for a in sys.argv[1:] if a]
    meta_only = "--meta-only" in argv
    positional = [a for a in argv if a != "--meta-only"]
    if len(positional) < 1:
        print("usage: sync-stream-parquet.py [--meta-only] <station_root>", file=sys.stderr)
        return 1
    root = Path(positional[0])
    info = read_json(root / "meta" / "info.json", {})
    live = read_json(root / "live" / "session.json", {})
    fps = float(info.get("fps") or 15)
    task = str(
        live.get("task")
        or "Perform egocentric manipulation tasks at the laboratory workbench"
    )
    total_frames = int(info.get("total_frames") or 0)
    episodes = load_episodes_index(root)
    jsonl_path = root / "data" / "chunk-000" / "file-000.jsonl"
    try:
        jsonl_mtime = int(jsonl_path.stat().st_mtime) if jsonl_path.is_file() else 0
    except OSError:
        jsonl_mtime = 0
    marker_path = root / "live" / "parquet_sync.json"
    marker = read_json(marker_path, {})
    episodes_key = len(episodes)

    write_tasks_jsonl(root, task)
    write_episodes_parquet(root, episodes, fps, task)
    if meta_only:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps(
                {
                    "total_frames": total_frames,
                    "jsonl_mtime": jsonl_mtime,
                    "episodes_tasks_list": True,
                    "episodes_count": episodes_key,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return 0

    if (
        marker.get("total_frames") == total_frames
        and marker.get("jsonl_mtime") == jsonl_mtime
        and marker.get("episodes_tasks_list") is True
        and marker.get("episodes_count") == episodes_key
        and (root / "meta" / "episodes" / "chunk-000" / "file-000.parquet").is_file()
    ):
        return 0
    rows = read_jsonl(jsonl_path)
    write_data_parquet(root, rows, fps, episodes)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps(
            {
                "total_frames": total_frames,
                "jsonl_mtime": jsonl_mtime,
                "episodes_tasks_list": True,
                "episodes_count": episodes_key,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
