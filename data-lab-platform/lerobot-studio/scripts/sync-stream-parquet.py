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


def write_data_parquet(root: Path, rows: list[dict], fps: float) -> None:
    n = len(rows)
    if n == 0:
        return
    frame_index = [int(r.get("frame_index", i)) for i, r in enumerate(rows)]
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


def write_episodes_parquet(root: Path, total_frames: int, fps: float, task: str) -> None:
    if total_frames <= 0:
        return
    duration = total_frames / fps if fps > 0 else 0.0
    row: dict = {
        "episode_index": 0.0,
        "length": float(total_frames),
        # LeRobot v3 expects list<string>; a bare string is iterated char-by-char ("E", "G", …).
        "tasks": [task],
        "task_index": 0.0,
        "dataset_from_index": 0.0,
        "dataset_to_index": float(total_frames),
        "data/chunk_index": 0.0,
        "data/file_index": 0.0,
        "chunk_index": 0.0,
        "file_index": 0.0,
    }
    for key in VIDEO_KEYS:
        row[f"videos/{key}/chunk_index"] = 0.0
        row[f"videos/{key}/file_index"] = 0.0
        row[f"videos/{key}/from_timestamp"] = 0.0
        row[f"videos/{key}/to_timestamp"] = duration
    columns = {k: pa.array([v]) for k, v in row.items() if k != "tasks"}
    columns["tasks"] = pa.array([[task]], type=pa.list_(pa.string()))
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
    jsonl_path = root / "data" / "chunk-000" / "file-000.jsonl"
    try:
        jsonl_mtime = int(jsonl_path.stat().st_mtime) if jsonl_path.is_file() else 0
    except OSError:
        jsonl_mtime = 0
    marker_path = root / "live" / "parquet_sync.json"
    marker = read_json(marker_path, {})

    write_tasks_jsonl(root, task)
    write_episodes_parquet(root, total_frames, fps, task)
    if meta_only:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps(
                {
                    "total_frames": total_frames,
                    "jsonl_mtime": jsonl_mtime,
                    "episodes_tasks_list": True,
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
        and (root / "meta" / "episodes" / "chunk-000" / "file-000.parquet").is_file()
    ):
        return 0
    rows = read_jsonl(jsonl_path)
    write_data_parquet(root, rows, fps)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps(
            {
                "total_frames": total_frames,
                "jsonl_mtime": jsonl_mtime,
                "episodes_tasks_list": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
