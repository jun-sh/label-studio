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

TASK_SHORT_NAMES = {
    DEFAULT_TASK.lower(): "工作台操作",
}

TASK_LABEL_FALLBACK = "未命名任务"


def abbreviate_task(task: str) -> str:
    trimmed = task.strip()
    if not trimmed:
        return TASK_LABEL_FALLBACK
    key = trimmed.lower()
    if key in TASK_SHORT_NAMES:
        return TASK_SHORT_NAMES[key]
    if len(trimmed) <= 32:
        return trimmed
    return trimmed[:30] + "…"


def format_collected_at(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return ""


def format_episode_list_task(ep: dict, full_task: str) -> str:
    """LeRobot sidebar: line1=#index, line2=duration, line3=tasks[0]."""
    short = abbreviate_task(full_task)
    length = int(ep.get("length") or 0)
    from_idx = int(ep.get("dataset_from_index") or 0)
    to_idx = int(ep.get("dataset_to_index") or from_idx + length)
    if length <= 0:
        length = max(0, to_idx - from_idx)
    collected = format_collected_at(ep.get("committed_at"))
    parts = [short]
    if collected:
        parts.append(collected)
    if length > 0:
        parts.append(f"{length}f")
    return " · ".join(parts)


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


def write_tasks_jsonl(root: Path, episodes: list[dict], full_task: str) -> None:
    meta = root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if episodes:
        for ep in episodes:
            idx = int(ep.get("episode_index", len(lines)))
            label = format_episode_list_task(ep, full_task)
            lines.append(
                json.dumps({"task_index": idx, "task": label}, ensure_ascii=False)
            )
    else:
        lines.append(
            json.dumps({"task_index": 0, "task": abbreviate_task(full_task)}, ensure_ascii=False)
        )
    (meta / "tasks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    matches: list[dict] = []
    for ep in episodes:
        start = int(ep.get("dataset_from_index", 0))
        end = int(ep.get("dataset_to_index", start))
        if start <= frame_index < end:
            matches.append(ep)
    if not matches:
        return 0
    # When legacy tail overlaps a newer segment, prefer the higher episode_index.
    matches.sort(
        key=lambda ep: (
            -int(ep.get("episode_index", 0)),
            int(ep.get("dataset_to_index", 0)) - int(ep.get("dataset_from_index", 0)),
        )
    )
    return int(matches[0].get("episode_index", 0))


def scalar_feature_keys(info: dict) -> list[str]:
    features = info.get("features") or {}
    keys = [k for k, spec in features.items() if spec.get("dtype") != "video"]
    if keys:
        return keys
    return ["observation.state", "observation.pose", "observation.hands", "action"]


def default_feature_vector(key: str, info: dict) -> list:
    features = info.get("features") or {}
    spec = features.get(key) or {}
    shape = spec.get("shape") or [1]
    size = 1
    for dim in shape:
        size *= int(dim)
    dtype = str(spec.get("dtype") or "float32")
    zero = 0 if dtype.startswith("int") else 0.0
    if key == "observation.pose" and size == 7:
        return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    return [zero] * size


def coerce_feature_vector(raw, key: str, info: dict) -> list:
    default = default_feature_vector(key, info)
    if not isinstance(raw, list):
        return default
    size = len(default)
    if len(raw) < size:
        return raw + [default[i] for i in range(len(raw), size)]
    if len(raw) > size:
        return raw[:size]
    return raw


def parquet_list_type(key: str, info: dict) -> pa.DataType:
    features = info.get("features") or {}
    spec = features.get(key) or {}
    dtype = str(spec.get("dtype") or "float32")
    if dtype.startswith("int"):
        value_type = pa.int64()
    elif dtype == "float64":
        value_type = pa.float64()
    else:
        value_type = pa.float32()
    return pa.list_(value_type)


def data_parquet_row_count(path: Path) -> int:
    if not path.is_file():
        return -1
    try:
        return pq.read_metadata(path).num_rows
    except Exception:
        return -1


def write_data_parquet(root: Path, rows: list[dict], fps: float, episodes: list[dict]) -> None:
    info = read_json(root / "meta" / "info.json", {})
    total_frames = int(info.get("total_frames") or 0)
    if total_frames <= 0 and rows:
        total_frames = max(int(r.get("frame_index", 0)) for r in rows) + 1
    if total_frames <= 0:
        return

    scalar_keys = scalar_feature_keys(info)
    sparse: dict[int, dict] = {}
    for row in rows:
        sparse[int(row.get("frame_index", len(sparse)))] = row

    last_vectors = {key: default_feature_vector(key, info) for key in scalar_keys}
    frame_index_col: list[int] = []
    episode_index_col: list[int] = []
    index_col: list[int] = []
    task_index_col: list[int] = []
    timestamp_col: list[float] = []
    feature_cols: dict[str, list[list]] = {key: [] for key in scalar_keys}

    for frame in range(total_frames):
        src = sparse.get(frame)
        frame_index_col.append(frame)
        index_col.append(frame)
        episode_index_col.append(
            episode_index_for_frame(episodes, frame) if episodes else 0
        )
        task_index_col.append(
            episode_index_for_frame(episodes, frame) if episodes else 0
        )
        timestamp_col.append(float(frame) / fps if fps > 0 else 0.0)
        for key in scalar_keys:
            if src is not None and key in src:
                last_vectors[key] = coerce_feature_vector(src[key], key, info)
            feature_cols[key].append(last_vectors[key][:])

    table_cols: dict[str, pa.Array] = {
        "frame_index": pa.array(frame_index_col, type=pa.int64()),
        "episode_index": pa.array(episode_index_col, type=pa.int64()),
        "index": pa.array(index_col, type=pa.int64()),
        "task_index": pa.array(task_index_col, type=pa.int64()),
        "timestamp": pa.array(timestamp_col, type=pa.float64()),
    }
    for key in scalar_keys:
        table_cols[key] = pa.array(feature_cols[key], type=parquet_list_type(key, info))

    out = root / "data" / "chunk-000" / "file-000.parquet"
    _atomic_parquet_write(pa.table(table_cols), out)


def _episode_row(ep: dict, fps: float, full_task: str) -> dict:
    length = int(ep.get("length") or 0)
    from_idx = int(ep.get("dataset_from_index") or 0)
    to_idx = int(ep.get("dataset_to_index") or from_idx + length)
    if length <= 0:
        length = max(0, to_idx - from_idx)
    duration = length / fps if fps > 0 else 0.0
    task = format_episode_list_task(ep, full_task)
    ep_index = int(ep.get("episode_index", 0))
    row: dict = {
        "episode_index": float(ep_index),
        "length": float(length),
        "task_index": float(ep_index),
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

    rows = [_episode_row(ep, fps, task) for ep in episodes]
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
    task = str(live.get("task") or "").strip()
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

    write_tasks_jsonl(root, episodes, task)
    write_episodes_parquet(root, episodes, fps, task)
    if meta_only:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps(
                {
                    "total_frames": total_frames,
                    "jsonl_mtime": jsonl_mtime,
                    "episodes_tasks_list": True,
                    "episode_display_v4": True,
                    "episodes_count": episodes_key,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return 0

    data_out = root / "data" / "chunk-000" / "file-000.parquet"
    data_rows = data_parquet_row_count(data_out)

    if (
        marker.get("total_frames") == total_frames
        and marker.get("jsonl_mtime") == jsonl_mtime
        and marker.get("episodes_tasks_list") is True
        and marker.get("episode_display_v4") is True
        and marker.get("episodes_count") == episodes_key
        and marker.get("data_dense") is True
        and data_rows == total_frames
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
                "episode_display_v4": True,
                "episodes_count": episodes_key,
                "data_dense": True,
                "data_rows": total_frames,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
