"""Canonical EGO parquet IO (annotations + episodes metadata)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

ANNOTATIONS_COLUMNS = ("episode_index", "frame_index", "subtask_index", "subtask_name")

EPISODE_META_STRING_COLS = (
    "station_id",
    "embodiment",
    "task_id",
    "annotation_status",
    "pipeline_version",
    "extended_info",
)

EPISODE_QUALITY_COLS = (
    "quality_valid_hand_ratio",
    "quality_mean_jitter",
)

HAND_POSE_LEFT = "observation.hand_pose_left"
HAND_POSE_RIGHT = "observation.hand_pose_right"


def parse_extended_info(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def serialize_extended_info(obj: dict[str, Any]) -> str:
    return json.dumps(obj if isinstance(obj, dict) else {}, ensure_ascii=False)


def merge_extended_info(existing: Any, updates: dict[str, Any]) -> str:
    base = parse_extended_info(existing)
    base.update(updates)
    return serialize_extended_info(base)


def annotations_parquet_path(root: Path) -> Path:
    return root / "meta" / "annotations.parquet"


def episodes_parquet_path(root: Path) -> Path:
    return root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"


def data_parquet_path(root: Path) -> Path:
    return root / "data" / "chunk-000" / "file-000.parquet"


def atomic_write_parquet(table: pa.Table, path: Path, *, expected_rows: int | None = None) -> None:
    if expected_rows is not None and table.num_rows != expected_rows:
        raise ValueError(f"row count mismatch: {table.num_rows} != {expected_rows}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp)
    try:
        verify = pq.read_table(tmp)
        if expected_rows is not None and verify.num_rows != expected_rows:
            raise ValueError(f"tmp verify row count: {verify.num_rows} != {expected_rows}")
        if set(verify.column_names) != set(table.column_names):
            raise ValueError("tmp verify column names mismatch")
    except Exception:
        if tmp.is_file():
            tmp.unlink()
        raise
    tmp.replace(path)


def ensure_annotations_skeleton(root: Path) -> None:
    path = annotations_parquet_path(root)
    if path.is_file():
        table = pq.read_table(path)
        if "episode_index" in table.column_names:
            return
        n = table.num_rows
        migrated = pa.table(
            {
                "episode_index": pa.array([0] * n, type=pa.int64()),
                "frame_index": table["frame_index"],
                "subtask_index": table["subtask_index"],
                "subtask_name": table["subtask_name"],
            }
        )
        atomic_write_parquet(migrated, path, expected_rows=n)
        return
    table = pa.table(
        {
            "episode_index": pa.array([], type=pa.int64()),
            "frame_index": pa.array([], type=pa.int64()),
            "subtask_index": pa.array([], type=pa.int64()),
            "subtask_name": pa.array([], type=pa.string()),
        }
    )
    atomic_write_parquet(table, path, expected_rows=0)


def load_info(root: Path) -> dict[str, Any]:
    info_path = root / "meta" / "info.json"
    if not info_path.is_file():
        raise FileNotFoundError(f"Dataset not found: {info_path}")
    return json.loads(info_path.read_text(encoding="utf-8"))


def scalar_features(info: dict[str, Any]) -> list[str]:
    feats = info.get("features") or {}
    return sorted(k for k, spec in feats.items() if spec.get("dtype") != "video")


def episode_row(root: Path, episode_index: int) -> dict[str, Any]:
    path = episodes_parquet_path(root)
    if not path.is_file():
        raise FileNotFoundError(f"missing episodes parquet: {path}")
    table = pq.read_table(path)
    for i in range(table.num_rows):
        if int(table["episode_index"][i].as_py()) == episode_index:
            return {name: table[name][i].as_py() for name in table.column_names}
    raise KeyError(f"episode_index {episode_index} not found")


def episode_meta_public(row: dict[str, Any]) -> dict[str, Any]:
    ratio = row.get("quality_valid_hand_ratio")
    jitter = row.get("quality_mean_jitter")
    return {
        "station_id": str(row.get("station_id") or "unknown"),
        "embodiment": str(row.get("embodiment") or "human_demo"),
        "task_id": str(row.get("task_id") or ""),
        "annotation_status": str(row.get("annotation_status") or "raw"),
        "quality_valid_hand_ratio": None if ratio is None or (isinstance(ratio, float) and math.isnan(ratio)) else float(ratio),
        "quality_mean_jitter": None if jitter is None or (isinstance(jitter, float) and math.isnan(jitter)) else float(jitter),
    }


def episode_total_frames(root: Path, episode_index: int, info: dict[str, Any]) -> int:
    row = episode_row(root, episode_index)
    length = int(row.get("length") or 0)
    if length > 0:
        return length
    from_idx = int(row.get("dataset_from_index") or 0)
    to_idx = int(row.get("dataset_to_index") or from_idx)
    if to_idx > from_idx:
        return to_idx - from_idx
    data_path = data_parquet_path(root)
    if data_path.is_file():
        table = pq.read_table(data_path, filters=[("episode_index", "=", episode_index)])
        return int(table.num_rows)
    return int(info.get("total_frames") or 0)


def read_annotation_boundaries(root: Path, episode_index: int) -> list[dict[str, Any]]:
    ensure_annotations_skeleton(root)
    path = annotations_parquet_path(root)
    table = pq.read_table(path)
    if table.num_rows == 0:
        return []
    rows: list[dict[str, Any]] = []
    for i in range(table.num_rows):
        ep = int(table["episode_index"][i].as_py())
        if ep != episode_index:
            continue
        rows.append(
            {
                "episode_index": ep,
                "frame_index": int(table["frame_index"][i].as_py()),
                "subtask_index": int(table["subtask_index"][i].as_py()),
                "subtask_name": str(table["subtask_name"][i].as_py()),
            }
        )
    rows.sort(key=lambda r: r["frame_index"])
    return rows


def segments_from_boundaries(boundaries: list[dict[str, Any]], total_frames: int) -> list[dict[str, Any]]:
    if not boundaries:
        return []
    sorted_b = sorted(boundaries, key=lambda r: r["frame_index"])
    out: list[dict[str, Any]] = []
    for i, row in enumerate(sorted_b):
        start = int(row["frame_index"])
        end = int(sorted_b[i + 1]["frame_index"]) - 1 if i + 1 < len(sorted_b) else total_frames - 1
        out.append(
            {
                "start_frame": start,
                "end_frame": max(start, end),
                "subtask_index": int(row["subtask_index"]),
                "subtask_name": str(row["subtask_name"]),
            }
        )
    return out


def write_annotations_for_episode(
    root: Path,
    episode_index: int,
    subtasks: list[dict[str, Any]],
) -> int:
    ensure_annotations_skeleton(root)
    path = annotations_parquet_path(root)
    table = pq.read_table(path)
    expected_rows = table.num_rows

    keep_rows: list[dict[str, Any]] = []
    for i in range(table.num_rows):
        row = {col: table[col][i].as_py() for col in ANNOTATIONS_COLUMNS}
        if int(row["episode_index"]) == episode_index:
            continue
        keep_rows.append(row)

    new_rows: list[dict[str, Any]] = []
    for seg in sorted(subtasks, key=lambda s: int(s.get("start_frame", 0))):
        new_rows.append(
            {
                "episode_index": episode_index,
                "frame_index": int(seg["start_frame"]),
                "subtask_index": int(seg.get("subtask_index", len(new_rows))),
                "subtask_name": str(seg.get("subtask_name") or ""),
            }
        )

    all_rows = keep_rows + new_rows
    new_table = pa.table(
        {
            "episode_index": pa.array([r["episode_index"] for r in all_rows], type=pa.int64()),
            "frame_index": pa.array([r["frame_index"] for r in all_rows], type=pa.int64()),
            "subtask_index": pa.array([r["subtask_index"] for r in all_rows], type=pa.int64()),
            "subtask_name": pa.array([r["subtask_name"] for r in all_rows], type=pa.string()),
        }
    )
    atomic_write_parquet(new_table, path, expected_rows=len(all_rows))
    return len(new_rows)


def update_episode_after_subtask_save(root: Path, episode_index: int) -> None:
    path = episodes_parquet_path(root)
    table = pq.read_table(path)
    expected_rows = table.num_rows

    target_idx: int | None = None
    prior_status = "raw"
    for i in range(table.num_rows):
        if int(table["episode_index"][i].as_py()) == episode_index:
            target_idx = i
            if "annotation_status" in table.column_names:
                prior_status = str(table["annotation_status"][i].as_py() or "raw")
            break
    if target_idx is None:
        raise KeyError(f"episode_index {episode_index} not found")

    columns: dict[str, pa.ChunkedArray] = {}
    for name in table.column_names:
        col = table.column(name)
        if name == "annotation_status" and prior_status in ("raw", "hand_done"):
            values = col.to_pylist()
            values[target_idx] = "subtask_done"
            columns[name] = pa.array(values, type=col.type)
        elif name == "extended_info":
            values = col.to_pylist()
            values[target_idx] = merge_extended_info(values[target_idx], {"last_subtask_save": True})
            columns[name] = pa.array(values, type=col.type)
        else:
            columns[name] = col

    atomic_write_parquet(pa.table(columns), path, expected_rows=expected_rows)


def read_hand_poses_for_episode(root: Path, episode_index: int) -> list[dict[str, Any]]:
    info = load_info(root)
    feats = info.get("features") or {}
    if HAND_POSE_LEFT not in feats and HAND_POSE_RIGHT not in feats:
        return []
    data_path = data_parquet_path(root)
    if not data_path.is_file():
        return []
    table = pq.read_table(data_path, filters=[("episode_index", "=", episode_index)])
    if table.num_rows == 0:
        return []
    fi_col = table["frame_index"].to_pylist()
    order = sorted(range(len(fi_col)), key=lambda i: int(fi_col[i]))
    out: list[dict[str, Any]] = []
    has_left = HAND_POSE_LEFT in table.column_names
    has_right = HAND_POSE_RIGHT in table.column_names
    for i in order:
        item: dict[str, Any] = {"frame_index": int(fi_col[i])}
        if has_left:
            item["hand_pose_left"] = list(table[HAND_POSE_LEFT][i].as_py())
        if has_right:
            item["hand_pose_right"] = list(table[HAND_POSE_RIGHT][i].as_py())
        out.append(item)
    return out
