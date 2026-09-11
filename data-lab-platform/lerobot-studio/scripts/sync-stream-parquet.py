#!/usr/bin/env python3
"""Rebuild v3.0 meta/data parquet artifacts from live stream jsonl (viewer compatibility)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

import re
from datetime import datetime, timezone


def _bootstrap_ego_platform() -> None:
    here = Path(__file__).resolve()
    candidates = [Path("/ego-platform/src")]
    for parent in here.parents:
        candidates.append(parent / "ego-platform" / "src")
    for candidate in candidates:
        if (candidate / "ego_platform").is_dir():
            sys.path.insert(0, str(candidate))
            return


_bootstrap_ego_platform()
try:
    from ego_platform.lerobot.data_schema import (
        cast_data_table_to_info,
        feature_arrow_type,
        values_to_feature_array,
    )
    from ego_platform.lerobot.io import (
        write_episodes_parquet as ego_write_episodes_parquet,
        write_tasks_parquet as ego_write_tasks_parquet,
    )
except ImportError:
    cast_data_table_to_info = None
    feature_arrow_type = None
    values_to_feature_array = None
    ego_write_episodes_parquet = None
    ego_write_tasks_parquet = None

PIPELINE_VERSION = "1.0.0"
VIEWER_SCAFFOLD_FRAMES = 1

EPISODE_META_STRING_COLS = (
    "station_id",
    "embodiment",
    "task_id",
    "annotation_status",
    "pipeline_version",
    "extended_info",
)

EPISODE_META_FLOAT_COLS = (
    "quality_valid_hand_ratio",
    "quality_mean_jitter",
)

EPISODE_INT_COLS = (
    "episode_index",
    "length",
    "task_index",
    "dataset_from_index",
    "dataset_to_index",
    "data/chunk_index",
    "data/file_index",
    "chunk_index",
    "file_index",
)

EPISODE_META_DEFAULTS = {
    "station_id": "unknown",
    "embodiment": "human_demo",
    "task_id": "",
    "annotation_status": "raw",
    "pipeline_version": PIPELINE_VERSION,
    "extended_info": "{}",
    "quality_valid_hand_ratio": None,
    "quality_mean_jitter": None,
}

VIDEO_KEYS = [
    "observation.images.camera_front_left",
    "observation.images.camera_front_right",
    "observation.images.camera_rear_left",
    "observation.images.camera_rear_right",
]


def video_keys_from_info(info: dict) -> list[str]:
    features = info.get("features") or {}
    keys = [k for k, spec in features.items() if isinstance(spec, dict) and spec.get("dtype") == "video"]
    return keys if keys else list(VIDEO_KEYS)

LEGACY_PLACEHOLDER_TASK = (
    "Perform egocentric manipulation tasks at the laboratory workbench"
)
TASK_LABEL_FALLBACK = "未命名任务"
_FRAME_SUFFIX_RE = re.compile(r" · \d+f$")


def station_short_name(station_id: str) -> str:
    sid = str(station_id or "").strip().lower()
    match = re.match(r"ego-lan-(\d+)", sid)
    if match:
        return f"EGO-{match.group(1)}"
    if sid.startswith("ego-"):
        parts = [p for p in sid.removeprefix("ego-").split("-") if p]
        if parts and parts[-1].isdigit():
            return f"EGO-{parts[-1]}"
    if sid:
        return sid.upper().replace("_", "-")
    return "EGO"


def session_id_short(session_id: str) -> str:
    token = str(session_id or "").strip()
    if token.lower().startswith("sess_"):
        token = token[5:]
    return token[:8].lower()


def format_session_date(created_at: str | None) -> str:
    if not created_at:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%m-%d")


def read_session_registry(root: Path) -> dict:
    return read_json(root / "live" / "session-registry.json", {"sessions": {}})


def session_started_at(root: Path, session_id: str) -> str | None:
    if not session_id:
        return None
    sessions = read_session_registry(root).get("sessions") or {}
    started = sessions.get(session_id, {}).get("startedAt")
    return str(started) if started else None


def is_auto_generated_task_label(task: str | None) -> bool:
    trimmed = str(task or "").strip()
    if not trimmed:
        return False
    import re

    return bool(
        re.match(
            r"^[A-Z0-9-]+\s·\s[a-f0-9]{8}\s·\s\d{2}-\d{2}(?:\s·\s\d+f|\s*\(\d+f\))?$",
            trimmed,
            re.IGNORECASE,
        )
    )


def auto_task_name(*, station_id: str, session_id: str, created_at: str | None) -> str:
    return (
        f"{station_short_name(station_id)} · "
        f"{session_id_short(session_id)} · "
        f"{format_session_date(created_at)}"
    )


def is_legacy_placeholder_task(task: str | None) -> bool:
    return str(task or "").strip().lower() == LEGACY_PLACEHOLDER_TASK.lower()


def resolve_task_name(
    *,
    explicit: str | None,
    station_id: str,
    session_id: str,
    created_at: str | None,
) -> str:
    label = str(explicit or "").strip()
    if label and not is_legacy_placeholder_task(label):
        return label
    return auto_task_name(
        station_id=station_id,
        session_id=session_id,
        created_at=created_at,
    )


def format_episode_display_task(base_task: str, length: int) -> str:
    base = str(base_task or "").strip()
    if is_legacy_placeholder_task(base):
        base = ""
    if not base:
        base = TASK_LABEL_FALLBACK
    if length > 0:
        return f"{base} · {length}f"
    return base


def format_episode_list_task(
    ep: dict,
    full_task: str,
    *,
    root: Path | None = None,
    station_id: str = "",
) -> str:
    """LeRobot sidebar line 3: prefer per-episode title from episodes-index."""
    session_id = str(ep.get("session_id") or "").strip()
    title = str(ep.get("title") or "").strip()
    if not session_id and title.startswith("sess_"):
        session_id = title
    if session_id.startswith("sess_") and root is not None and station_id:
        base = resolve_task_name(
            explicit=None,
            station_id=station_id,
            session_id=session_id,
            created_at=session_started_at(root, session_id),
        )
        length = int(ep.get("length") or 0)
        from_idx = int(ep.get("dataset_from_index") or 0)
        to_idx = int(ep.get("dataset_to_index") or from_idx + length)
        if length <= 0:
            length = max(0, to_idx - from_idx)
        return format_episode_display_task(base, length)
    if title and not title.startswith("sess_"):
        return title
    length = int(ep.get("length") or 0)
    from_idx = int(ep.get("dataset_from_index") or 0)
    to_idx = int(ep.get("dataset_to_index") or from_idx + length)
    if length <= 0:
        length = max(0, to_idx - from_idx)
    return format_episode_display_task(full_task, length)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return default


def lerobot_owns_data(root: Path) -> bool:
    """Official LeRobot derive owns data/ and meta/episodes/ — sync must not rewrite them."""
    marker = read_json(root / "live" / "parquet_sync.json", {})
    jsonl = root / "data" / "chunk-000" / "file-000.jsonl"
    if jsonl.is_file():
        return False
    if marker.get("lerobot_data_owned") is True:
        return True
    data_pq = root / "data" / "chunk-000" / "file-000.parquet"
    return data_pq.is_file() and not jsonl.is_file()


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


def write_station_tasks(
    root: Path, episodes: list[dict], full_task: str, *, skip_tasks_parquet: bool = False
) -> None:
    if skip_tasks_parquet:
        return
    meta = root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    station_id = root.name
    task_rows: list[dict] = []
    if episodes:
        for ep in episodes:
            idx = int(ep.get("episode_index", len(task_rows)))
            label = format_episode_list_task(ep, full_task, root=root, station_id=station_id)
            task_rows.append({"task_index": idx, "task": label})
    else:
        task_rows.append(
            {
                "task_index": 0,
                "task": format_episode_display_task(full_task, 0),
            }
        )
    if ego_write_tasks_parquet is not None:
        ego_write_tasks_parquet(root, task_rows)
        return
    _write_tasks_parquet_rows(root, task_rows)


def _write_tasks_parquet_rows(root: Path, rows: list[dict]) -> None:
    if ego_write_tasks_parquet is None:
        raise RuntimeError(
            "ego_platform.lerobot.io.write_tasks_parquet unavailable; "
            "set PYTHONPATH to ego-platform/src before running sync-stream-parquet"
        )
    ego_write_tasks_parquet(root, rows)
    legacy = root / "meta" / "tasks.jsonl"
    if legacy.is_file():
        legacy.unlink()


def write_tasks_parquet(root: Path, episodes: list[dict], full_task: str) -> None:
    """LeRobot v3 canonical meta/tasks.parquet."""
    station_id = root.name
    rows: list[dict] = []
    if episodes:
        for ep in episodes:
            idx = int(ep.get("episode_index", len(rows)))
            rows.append(
                {
                    "task_index": idx,
                    "task": format_episode_list_task(ep, full_task, root=root, station_id=station_id),
                }
            )
    else:
        rows.append(
            {
                "task_index": 0,
                "task": format_episode_display_task(full_task, 0),
            }
        )
    _write_tasks_parquet_rows(root, rows)


def _atomic_parquet_write(table: pa.Table, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    pq.write_table(table, tmp)
    tmp.replace(out)


def load_episode_meta_defaults(root: Path) -> dict:
    raw = read_json(root / "live" / "episode-meta.json", {})
    if not isinstance(raw, dict):
        return dict(EPISODE_META_DEFAULTS)
    out = dict(EPISODE_META_DEFAULTS)
    for key in EPISODE_META_STRING_COLS:
        if key in raw and raw[key] is not None:
            out[key] = str(raw[key])
    for key in EPISODE_META_FLOAT_COLS:
        if key in raw and raw[key] is not None:
            try:
                out[key] = float(raw[key])
            except (TypeError, ValueError):
                out[key] = None
    if not isinstance(out.get("extended_info"), str):
        out["extended_info"] = json.dumps(out.get("extended_info") or {}, ensure_ascii=False)
    return out


def episode_meta_for_index_entry(ep: dict, defaults: dict) -> dict:
    nested = ep.get("episode_meta") if isinstance(ep.get("episode_meta"), dict) else {}
    merged = {**defaults, **nested}
    if not isinstance(merged.get("extended_info"), str):
        merged["extended_info"] = json.dumps(merged.get("extended_info") or {}, ensure_ascii=False)
    return merged


def ensure_annotations_skeleton(root: Path) -> None:
    out = root / "meta" / "annotations.parquet"
    if out.is_file():
        table = pq.read_table(out)
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
        _atomic_parquet_write(migrated, out)
        return
    table = pa.table(
        {
            "episode_index": pa.array([], type=pa.int64()),
            "frame_index": pa.array([], type=pa.int64()),
            "subtask_index": pa.array([], type=pa.int64()),
            "subtask_name": pa.array([], type=pa.string()),
        }
    )
    _atomic_parquet_write(table, out)


def load_episodes_index(root: Path) -> list[dict]:
    raw = read_json(root / "live" / "episodes-index.json", {})
    episodes = raw.get("episodes") if isinstance(raw, dict) else None
    if not isinstance(episodes, list) or not episodes:
        return []
    return sorted(episodes, key=lambda ep: int(ep.get("episode_index", 0)))


def row_episode_index(src: dict, episodes: list[dict], frame_index: int) -> int:
    if "episode_index" in src and src.get("episode_index") is not None:
        return int(src.get("episode_index"))
    return episode_index_for_frame(episodes, frame_index) if episodes else 0


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
    if key == "observation.head_pose" and size == 7:
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


def fallback_values_to_feature_array(values: list, spec: dict) -> pa.Array:
    """Container fallback when ego_platform is not installed on the image."""
    dtype = str(spec.get("dtype") or "float32")
    shape = spec.get("shape") or [1]
    size = 1
    for dim in shape:
        size *= int(dim)
    if dtype.startswith("int"):
        value_type = pa.int64()
    elif dtype == "float64":
        value_type = pa.float64()
    else:
        value_type = pa.float32()
    if size == 1:
        flat = [
            (v[0] if isinstance(v, (list, tuple)) and len(v) == 1 else v)
            for v in values
        ]
        return pa.array(flat, type=value_type)
    return pa.array(values, type=pa.list_(value_type, size))


def parquet_feature_type(key: str, info: dict) -> pa.DataType:
    features = info.get("features") or {}
    spec = features.get(key) or {}
    if feature_arrow_type is not None:
        return feature_arrow_type(spec)
    dtype = str(spec.get("dtype") or "float32")
    shape = spec.get("shape") or [1]
    size = 1
    for dim in shape:
        size *= int(dim)
    if dtype.startswith("int"):
        value_type = pa.int64()
    elif dtype == "float64":
        value_type = pa.float64()
    else:
        value_type = pa.float32()
    if size == 1:
        return value_type
    return pa.list_(value_type, size)


def data_parquet_row_count(path: Path) -> int:
    if not path.is_file():
        return -1
    try:
        return pq.read_metadata(path).num_rows
    except Exception:
        return -1


INT_DATA_COLS = frozenset({"frame_index", "episode_index", "index", "task_index"})
SYSTEM_FEATURE_SPECS = {
    "timestamp": {"dtype": "float32", "shape": [1], "names": None},
    "frame_index": {"dtype": "int64", "shape": [1], "names": None},
    "episode_index": {"dtype": "int64", "shape": [1], "names": None},
    "index": {"dtype": "int64", "shape": [1], "names": None},
    "task_index": {"dtype": "int64", "shape": [1], "names": None},
}


def ensure_system_features(info: dict) -> dict:
    features = info.get("features") if isinstance(info.get("features"), dict) else {}
    for key, spec in SYSTEM_FEATURE_SPECS.items():
        features.setdefault(key, spec)
    info["features"] = features
    return info


def finalize_data_table(table: pa.Table, features: dict) -> pa.Table:
    """Cast columns to LeRobot 0.4.x loader expectations (scalar shape-[1], int64 indices)."""
    if cast_data_table_to_info is not None:
        table = cast_data_table_to_info(table, features)
    cols: dict[str, pa.Array] = {}
    for name in table.column_names:
        col = table[name]
        if name in INT_DATA_COLS:
            cols[name] = pa.array([int(x or 0) for x in col.to_pylist()], type=pa.int64())
        else:
            cols[name] = col
    return pa.table(cols)


def write_meta_stats_json(root: Path) -> bool:
    """Write meta/stats.json (LeRobot training norm stats) when compute_stats is available."""
    info = read_json(root / "meta" / "info.json", {})
    features = info.get("features") or {}
    frames: list = []
    data_root = root / "data"
    if not data_root.is_dir():
        return False
    for parquet_path in sorted(data_root.rglob("*.parquet")):
        if "sensor_raw" in parquet_path.parts:
            continue
        try:
            frames.append(pq.read_table(parquet_path).to_pandas())
        except Exception:
            continue
    if not frames:
        return False
    import pandas as pd

    data_df = pd.concat(frames, ignore_index=True)
    try:
        import numpy as np
        from lerobot.datasets.compute_stats import DEFAULT_QUANTILES, get_feature_stats

        stats: dict = {}
        for feature_name, feature_info in features.items():
            dtype = str(feature_info.get("dtype") or "")
            if dtype in ("video", "image", "string") or feature_name not in data_df.columns:
                continue
            values = np.stack(data_df[feature_name].to_numpy())
            stats[feature_name] = get_feature_stats(
                values,
                axis=0,
                keepdims=values.ndim == 1,
                quantile_list=DEFAULT_QUANTILES,
            )
        if not stats:
            return False
        from lerobot.datasets.utils import write_stats

        write_stats(stats, root)
        return True
    except Exception as exc:
        print(f"stats.json skipped for {root}: {exc}", file=sys.stderr)
        return False


def write_data_parquet(root: Path, rows: list[dict], fps: float, episodes: list[dict]) -> None:
    if not rows:
        return
    info = ensure_system_features(read_json(root / "meta" / "info.json", {}))
    scalar_keys = scalar_feature_keys(info)
    rows_sorted = sorted(rows, key=lambda r: int(r.get("frame_index", 0)))
    frame_min = int(rows_sorted[0].get("frame_index", 0))

    frame_index_col: list[int] = []
    episode_index_col: list[int] = []
    index_col: list[int] = []
    task_index_col: list[int] = []
    timestamp_col: list[float] = []
    feature_cols: dict[str, list[list]] = {key: [] for key in scalar_keys}

    for seq, src in enumerate(rows_sorted):
        frame = int(src.get("frame_index", seq))
        frame_index_col.append(frame)
        index_col.append(seq)
        ep_idx = row_episode_index(src, episodes, frame)
        episode_index_col.append(ep_idx)
        task_index_col.append(
            int(src.get("task_index"))
            if src.get("task_index") is not None
            else ep_idx
        )
        timestamp_col.append(float(seq) / fps if fps > 0 else 0.0)
        for key in scalar_keys:
            feature_cols[key].append(coerce_feature_vector(src.get(key), key, info))

    features = info.get("features") or {}
    table_cols: dict[str, pa.Array] = {}
    for key, values in {
        "frame_index": frame_index_col,
        "episode_index": episode_index_col,
        "index": index_col,
        "task_index": task_index_col,
        "timestamp": timestamp_col,
        **feature_cols,
    }.items():
        spec = features.get(key) or {}
        if values_to_feature_array is not None and isinstance(spec, dict) and spec.get("dtype") != "video":
            table_cols[key] = values_to_feature_array(values, spec)
        elif key in feature_cols and isinstance(spec, dict) and spec.get("dtype") != "video":
            table_cols[key] = fallback_values_to_feature_array(values, spec)
        else:
            table_cols[key] = pa.array(values, type=parquet_feature_type(key, info))

    out = root / "data" / "chunk-000" / "file-000.parquet"
    table = finalize_data_table(pa.table(table_cols), features)
    _atomic_parquet_write(table, out)

    info["total_frames"] = len(rows_sorted)
    info["ingest_row_count"] = len(rows_sorted)
    info["frame_index_min"] = frame_min
    info["frame_index_max"] = int(rows_sorted[-1].get("frame_index", frame_min))
    (root / "meta" / "info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def _episode_row(
    ep: dict,
    fps: float,
    full_task: str,
    meta_defaults: dict,
    info: dict,
    *,
    root: Path,
    station_id: str,
) -> dict:
    length = int(ep.get("length") or 0)
    from_idx = int(ep.get("dataset_from_index") or 0)
    to_idx = int(ep.get("dataset_to_index") or from_idx + length)
    if length <= 0:
        length = max(0, to_idx - from_idx)
    duration = length / fps if fps > 0 else 0.0
    task = format_episode_list_task(ep, full_task, root=root, station_id=station_id)
    ep_index = int(ep.get("episode_index", 0))
    per_file = bool(ep.get("per_episode_file"))
    file_index = int(ep.get("data/file_index", ep.get("file_index", ep_index if per_file else 0)))
    global_frames = bool(ep.get("global_frame_indices"))
    if per_file and not global_frames:
        from_idx = 0
        to_idx = length
        duration = length / fps if fps > 0 else 0.0
    ep_meta = episode_meta_for_index_entry(ep, meta_defaults)
    row: dict = {
        "episode_index": ep_index,
        "length": length,
        "task_index": ep_index,
        "dataset_from_index": from_idx,
        "dataset_to_index": to_idx,
        "data/chunk_index": int(ep.get("data/chunk_index", 0)),
        "data/file_index": file_index,
        "chunk_index": int(ep.get("chunk_index", 0)),
        "file_index": file_index,
    }
    for key in EPISODE_META_STRING_COLS:
        row[key] = ep_meta.get(key, EPISODE_META_DEFAULTS[key])
    for key in EPISODE_META_FLOAT_COLS:
        val = ep_meta.get(key)
        row[key] = float(val) if val is not None else float("nan")
    for key in video_keys_from_info(info):
        v_chunk = int(ep.get(f"videos/{key}/chunk_index", 0))
        v_file = int(ep.get(f"videos/{key}/file_index", file_index))
        row[f"videos/{key}/chunk_index"] = v_chunk
        row[f"videos/{key}/file_index"] = v_file
        if per_file and not global_frames:
            row[f"videos/{key}/from_timestamp"] = 0.0
            row[f"videos/{key}/to_timestamp"] = duration
        else:
            row[f"videos/{key}/from_timestamp"] = float(from_idx) / fps if fps > 0 else 0.0
            row[f"videos/{key}/to_timestamp"] = float(to_idx) / fps if fps > 0 else duration
    row["tasks"] = task
    row["_duration"] = duration
    return row


def _episode_column_array(key: str, rows: list[dict]) -> pa.Array:
    if key in EPISODE_META_STRING_COLS:
        return pa.array([row[key] for row in rows], type=pa.string())
    if key in EPISODE_INT_COLS or (
        key.startswith("videos/") and (key.endswith("/chunk_index") or key.endswith("/file_index"))
    ):
        return pa.array([int(row[key]) for row in rows], type=pa.int64())
    if key in EPISODE_META_FLOAT_COLS:
        return pa.array([row[key] for row in rows], type=pa.float64())
    return pa.array([row[key] for row in rows], type=pa.float64())


def write_episodes_parquet(root: Path, episodes: list[dict], fps: float, task: str) -> None:
    meta_defaults = load_episode_meta_defaults(root)
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

    info = read_json(root / "meta" / "info.json", {})
    station_id = root.name
    rows = [
        _episode_row(ep, fps, task, meta_defaults, info, root=root, station_id=station_id)
        for ep in episodes
    ]
    if ego_write_episodes_parquet is not None:
        ego_write_episodes_parquet(root, rows)
        return
    columns: dict[str, pa.Array] = {}
    for key in rows[0]:
        if key == "tasks":
            continue
        if key == "_duration":
            continue
        columns[key] = _episode_column_array(key, rows)
    columns["tasks"] = pa.array([[row["tasks"]] for row in rows], type=pa.list_(pa.string()))
    table = pa.table(columns)
    out = root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    _atomic_parquet_write(table, out)


def write_empty_episodes_parquet(root: Path, fps: float, task: str) -> None:
    """Zero-row episodes table — layout-only scaffold (no sidebar #0)."""
    meta_defaults = load_episode_meta_defaults(root)
    info = read_json(root / "meta" / "info.json", {})
    template = _episode_row(
        {
            "episode_index": 0,
            "length": 0,
            "dataset_from_index": 0,
            "dataset_to_index": 0,
            "title": task,
        },
        fps,
        task,
        meta_defaults,
        info,
        root=root,
        station_id=root.name,
    )
    columns: dict[str, pa.Array] = {}
    for key in template:
        if key in ("tasks", "_duration"):
            continue
        columns[key] = _episode_column_array(key, [])
    columns["tasks"] = pa.array([], type=pa.list_(pa.string()))
    out = root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    _atomic_parquet_write(pa.table(columns), out)


def write_placeholder_videos(root: Path, info: dict) -> None:
    fps = float(info.get("fps") or 30)
    duration = max(1.0 / fps, 0.04)
    features = info.get("features") or {}
    for key in video_keys_from_info(info):
        feat = features.get(key, {})
        shape = feat.get("shape") or [800, 1280, 3]
        h, w = int(shape[0]), int(shape[1])
        out = root / "videos" / key / "chunk-000" / "file-000.mp4"
        if out.is_file() and out.stat().st_size > 0:
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s={w}x{h}:d={duration}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-frames:v",
                "1",
                str(out),
            ],
            check=False,
        )


def write_viewer_scaffold(root: Path) -> int:
    """Minimal v3 dataset so LeRobot opens dockview panes before first real ingest."""
    info_path = root / "meta" / "info.json"
    if not info_path.is_file():
        return 1
    info = read_json(info_path, {})
    station_id = root.name
    live = read_json(root / "live" / "session.json", {})
    task = resolve_live_task(root, live)
    total_frames = max(VIEWER_SCAFFOLD_FRAMES, int(info.get("total_frames") or 0))
    info["total_frames"] = total_frames
    info["total_episodes"] = 1
    info["splits"] = {"train": "0:1"}
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    fps = float(info.get("fps") or 30)
    write_station_tasks(root, [], task)
    ensure_annotations_skeleton(root)
    write_empty_episodes_parquet(root, fps, task)
    write_data_parquet(root, [], fps, [])
    write_placeholder_videos(root, info)
    marker_path = root / "live" / "parquet_sync.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps(
            {
                "viewer_scaffold": True,
                "total_frames": total_frames,
                "jsonl_mtime": 0,
                "episodes_tasks_list": True,
                "episode_display_v4": True,
                "episodes_count": 0,
                "data_dense": True,
                "data_rows": total_frames,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def main_data_parquet_shards(root: Path) -> list[Path]:
    return [p for p in sorted(root.glob("data/**/*.parquet")) if "sensor_raw" not in p.parts]


def sync_lerobot_info_frame_counts(root: Path, info: dict, episodes: list[dict]) -> None:
    """Align info.json totals with official LeRobot data shards (multi-session safe)."""
    shard_rows = sum(pq.read_metadata(p).num_rows for p in main_data_parquet_shards(root))
    if shard_rows > 0:
        info["total_frames"] = shard_rows
        info["ingest_row_count"] = shard_rows
    if episodes:
        info["total_episodes"] = len(episodes)
        info["frame_index_min"] = min(int(ep.get("dataset_from_index") or 0) for ep in episodes)
        info["frame_index_max"] = max(int(ep.get("dataset_to_index") or 0) for ep in episodes) - 1
    (root / "meta" / "info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def unit_manifest_published(root: Path) -> dict | None:
    manifest = read_json(root / "manifest" / "manifest.json", {})
    episodes = manifest.get("episodes") if isinstance(manifest, dict) else None
    if not isinstance(episodes, list) or not episodes:
        return None
    return manifest


def episodes_from_unit_manifest(manifest: dict) -> list[dict]:
    out: list[dict] = []
    for ep in manifest.get("episodes") or []:
        ep_index = int(ep.get("episode_index", len(out)))
        length = int(ep.get("frames") or 0)
        from_idx = int(ep.get("from_index", ep.get("dataset_from_index", 0)))
        to_idx = int(ep.get("to_index", from_idx + length))
        if length <= 0:
            length = max(0, to_idx - from_idx)
        out.append(
            {
                "episode_index": ep_index,
                "session_id": str(ep.get("session_id") or ""),
                "length": length,
                "dataset_from_index": from_idx,
                "dataset_to_index": to_idx,
                "data/chunk_index": 0,
                "data/file_index": ep_index,
                "file_index": ep_index,
                "per_episode_file": True,
                "global_frame_indices": True,
                "title": str(ep.get("session_id") or ""),
            }
        )
    return out


def remap_unit_table_to_global(
    table: pa.Table, *, episode_index: int, from_index: int, fps: float
) -> pa.Table:
    """Align per-unit derived rows with LeRobot global frame / episode indices."""
    cols: dict[str, pa.Array] = {}
    n = table.num_rows
    for name in table.column_names:
        cols[name] = table[name]
    local_frames = [
        int(x if x is not None else i)
        for i, x in enumerate(table["frame_index"].to_pylist())
    ]
    if len(set(local_frames)) <= 1 and n > 1:
        local_frames = list(range(n))
    cols["frame_index"] = pa.array([from_index + i for i in local_frames], type=pa.int64())
    cols["episode_index"] = pa.array([episode_index] * n, type=pa.int64())
    cols["index"] = pa.array([from_index + i for i in range(n)], type=pa.int64())
    cols["task_index"] = pa.array([episode_index] * n, type=pa.int64())
    if "timestamp" in table.column_names:
        cols["timestamp"] = pa.array(
            [float(from_index + i) / fps if fps > 0 else 0.0 for i in range(n)],
            type=pa.float32(),
        )
    return pa.table(cols)


def resolve_live_task(root: Path, live: dict) -> str:
    station_id = root.name
    session_id = str(live.get("sessionId") or "")
    live_task = str(live.get("task") or "").strip() or None
    explicit = live_task if live_task and not is_auto_generated_task_label(live_task) else None
    created_at = session_started_at(root, session_id) or live.get("startedAt")
    return resolve_task_name(
        explicit=explicit,
        station_id=station_id,
        session_id=session_id,
        created_at=created_at,
    )


def repair_corpus_task_dates(
    stream_root: Path, corpus_root: Path, *, viewer_sync: bool = True
) -> int:
    """Rewrite corpus task labels from stream session-registry startedAt."""
    station_id = stream_root.name
    sessions = read_session_registry(stream_root).get("sessions") or {}
    info_path = corpus_root / "meta" / "info.json"
    info = read_json(info_path, {})
    if not info:
        print(f"missing corpus info: {info_path}", file=sys.stderr)
        return 1

    history = (info.get("ego_platform") or {}).get("publish_history") or []
    if not history:
        print("no ego_platform.publish_history to repair", file=sys.stderr)
        return 1

    changed = 0
    for entry in history:
        session_id = str(entry.get("session_id") or "").strip()
        if not session_id:
            continue
        started_at = session_started_at(stream_root, session_id)
        base = auto_task_name(
            station_id=station_id,
            session_id=session_id,
            created_at=started_at,
        )
        frames = int(entry.get("frames") or 0)
        new_task = f"{base} ({frames}f)" if frames > 0 else base
        if entry.get("task") != new_task:
            entry["task"] = new_task
            changed += 1

    task_rows: list[dict] = []
    for entry in sorted(history, key=lambda row: int(row.get("episode_index", 0))):
        idx = int(entry.get("episode_index", len(task_rows)))
        task_rows.append({"task_index": idx, "task": str(entry.get("task") or "")})
    if ego_write_tasks_parquet is not None:
        ego_write_tasks_parquet(corpus_root, task_rows)
    else:
        _write_tasks_parquet_rows(corpus_root, task_rows)
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    live_path = stream_root / "live" / "session.json"
    live = read_json(live_path, {})
    session_id = str(live.get("sessionId") or "").strip()
    if session_id:
        started_at = session_started_at(stream_root, session_id)
        if started_at:
            live["startedAt"] = started_at
        live["task"] = resolve_live_task(stream_root, live)
        live_path.write_text(json.dumps(live, indent=2) + "\n", encoding="utf-8")

    print(f"repaired {changed} corpus task label(s)")
    sync_repaired_meta_to_viewer_samples(corpus_root, viewer_sync=viewer_sync)
    return 0


def sync_repaired_meta_to_viewer_samples(corpus_root: Path, *, viewer_sync: bool = True) -> None:
    """Copy repaired meta into samples/ and refresh lerobot bundled volume."""
    slug = corpus_root.name
    samples_root = corpus_root.parent.parent / "samples" / slug / "dataset"
    if not samples_root.parent.is_dir():
        print(f"viewer samples dir missing: {samples_root.parent}", file=sys.stderr)
        return

    samples_meta = samples_root / "meta"
    samples_meta.mkdir(parents=True, exist_ok=True)
    for name in ("tasks.parquet", "info.json"):
        src = corpus_root / "meta" / name
        if not src.is_file():
            continue
        dest = samples_meta / name
        if name.endswith(".parquet"):
            shutil.copy2(src, dest)
        else:
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"synced {name} -> {dest}")

    if not viewer_sync:
        print("viewer bundled sync skipped (--no-viewer-sync)")
        return

    container = os.environ.get("LEROBOT_CONTAINER", "data-lab-lerobot-1")
    try:
        listed = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if container not in (listed.stdout or ""):
            print(f"viewer sync skipped: container {container} not running", file=sys.stderr)
            return
        proc = subprocess.run(
            ["docker", "exec", container, "sh", "/app/ingest-bundled-datasets.sh"],
            check=False,
        )
        if proc.returncode == 0:
            print("viewer bundled sync complete")
        else:
            print(f"viewer bundled sync failed (exit {proc.returncode})", file=sys.stderr)
    except FileNotFoundError:
        print("viewer sync skipped: docker not available", file=sys.stderr)


def validate_unit_manifest_data_shards(root: Path) -> list[str]:
    """Ensure published L2 parquet shards match manifest episode indices (convert slice gate)."""
    manifest = unit_manifest_published(root)
    if not manifest:
        return []
    errors: list[str] = []
    for ep in episodes_from_unit_manifest(manifest):
        ep_index = int(ep.get("episode_index", -1))
        length = int(ep.get("length") or 0)
        from_idx = int(ep.get("dataset_from_index") or 0)
        session_id = str(ep.get("session_id") or "")
        pq_path = root / "data" / "chunk-000" / f"file-{ep_index:03d}.parquet"
        if not pq_path.is_file():
            errors.append(f"missing data shard {pq_path.relative_to(root)} for {session_id}")
            continue
        table = pq.read_table(pq_path)
        if "episode_index" not in table.column_names:
            errors.append(f"{pq_path.name} missing episode_index column")
            continue
        ep_vals = {int(x or 0) for x in table["episode_index"].to_pylist()}
        if ep_vals != {ep_index}:
            errors.append(f"{pq_path.name} episode_index={sorted(ep_vals)} expected {{{ep_index}}}")
        if length > 0 and table.num_rows != length:
            errors.append(f"{pq_path.name} rows={table.num_rows} expected length={length}")
        if "frame_index" in table.column_names and table.num_rows > 0:
            frames = [int(x or 0) for x in table["frame_index"].to_pylist()]
            if min(frames) != from_idx or max(frames) != from_idx + table.num_rows - 1:
                errors.append(
                    f"{pq_path.name} frame_index range [{min(frames)},{max(frames)}] "
                    f"expected [{from_idx},{from_idx + table.num_rows - 1}]"
                )
    return errors


def republish_unit_data_shards(root: Path) -> int:
    """Copy derived/sess_*/data.parquet into data/chunk-000/file-{episode_index}.parquet."""
    manifest = unit_manifest_published(root)
    if not manifest:
        return 0
    info = ensure_system_features(read_json(root / "meta" / "info.json", {}))
    features = info.get("features") or {}
    fps = float(info.get("fps") or 30)
    episodes = episodes_from_unit_manifest(manifest)
    count = 0
    for ep in episodes:
        session_id = str(ep.get("session_id") or "").strip()
        ep_index = int(ep.get("episode_index", count))
        from_idx = int(ep.get("dataset_from_index") or 0)
        if not session_id:
            continue
        derived = root / "derived" / session_id / "data.parquet"
        if not derived.is_file():
            continue
        table = remap_unit_table_to_global(
            pq.read_table(derived),
            episode_index=ep_index,
            from_index=from_idx,
            fps=fps,
        )
        table = finalize_data_table(table, features)
        out = root / "data" / "chunk-000" / f"file-{ep_index:03d}.parquet"
        _atomic_parquet_write(table, out)
        count += 1
    if count:
        sync_unit_manifest_info(root, manifest)
        write_episodes_parquet(
            root,
            episodes,
            fps,
            resolve_live_task(root, read_json(root / "live" / "session.json", {})),
        )
        (root / "meta" / "info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        errors = validate_unit_manifest_data_shards(root)
        if errors:
            raise RuntimeError("; ".join(errors))
    return count


def sync_unit_manifest_info(root: Path, manifest: dict) -> int:
    total_frames = int(manifest.get("total_frames") or 0)
    episodes = episodes_from_unit_manifest(manifest)
    info = ensure_system_features(read_json(root / "meta" / "info.json", {}))
    info["total_frames"] = total_frames
    info["ingest_row_count"] = total_frames
    info["total_episodes"] = len(episodes)
    if total_frames > 0:
        info["frame_index_min"] = 0
        info["frame_index_max"] = total_frames - 1
    if episodes:
        info["splits"] = {"train": f"0:{len(episodes)}"}
    (root / "meta" / "info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return total_frames


def main() -> int:
    argv = [a for a in sys.argv[1:] if a]
    viewer_scaffold = "--viewer-scaffold" in argv
    meta_only = "--meta-only" in argv
    republish_units = "--republish-units" in argv
    repair_corpus = "--repair-corpus-task-dates" in argv
    no_viewer_sync = "--no-viewer-sync" in argv
    flags = {
        "--meta-only",
        "--viewer-scaffold",
        "--repair-corpus-task-dates",
        "--no-viewer-sync",
        "--republish-units",
    }
    positional = [a for a in argv if a not in flags]
    if republish_units:
        if len(positional) != 1:
            print("usage: sync-stream-parquet.py --republish-units <station_root>", file=sys.stderr)
            return 1
        root = Path(positional[0])
        try:
            count = republish_unit_data_shards(root)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        write_meta_stats_json(root)
        print(f"republished {count}")
        return 0
    if repair_corpus:
        if len(positional) != 2:
            print(
                "usage: sync-stream-parquet.py --repair-corpus-task-dates <stream_root> <corpus_root>",
                file=sys.stderr,
            )
            return 1
        return repair_corpus_task_dates(
            Path(positional[0]),
            Path(positional[1]),
            viewer_sync=not no_viewer_sync,
        )
    if len(positional) < 1:
        print(
            "usage: sync-stream-parquet.py [--meta-only|--viewer-scaffold|--repair-corpus-task-dates|--republish-units] <station_root> [corpus_root]",
            file=sys.stderr,
        )
        return 1
    root = Path(positional[0])
    if viewer_scaffold:
        return write_viewer_scaffold(root)
    info = read_json(root / "meta" / "info.json", {})
    live = read_json(root / "live" / "session.json", {})
    fps = float(info.get("fps") or 30)
    station_id = root.name
    task = resolve_live_task(root, live)
    total_frames = int(info.get("total_frames") or 0)
    unit_manifest = unit_manifest_published(root)
    if unit_manifest:
        episodes = episodes_from_unit_manifest(unit_manifest)
        total_frames = sync_unit_manifest_info(root, unit_manifest)
        episodes_key = len(episodes)
    else:
        episodes = load_episodes_index(root)
        episodes_key = len(episodes)
    jsonl_path = root / "data" / "chunk-000" / "file-000.jsonl"
    try:
        jsonl_mtime = int(jsonl_path.stat().st_mtime) if jsonl_path.is_file() else 0
    except OSError:
        jsonl_mtime = 0
    marker_path = root / "live" / "parquet_sync.json"
    marker = read_json(marker_path, {})
    episodes_key = len(episodes)

    lerobot_owned = lerobot_owns_data(root) or unit_manifest is not None
    write_station_tasks(root, episodes, task, skip_tasks_parquet=lerobot_owned)
    ensure_annotations_skeleton(root)
    if not lerobot_owned and not meta_only:
        write_episodes_parquet(root, episodes, fps, task)
        rows = read_jsonl(jsonl_path)
        if rows:
            write_data_parquet(root, rows, fps, episodes)
            total_frames = len(rows)
    if lerobot_owned and not unit_manifest:
        sync_lerobot_info_frame_counts(root, info, episodes)
    elif unit_manifest:
        write_episodes_parquet(root, episodes, fps, task)
        republish_unit_data_shards(root)
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
                    **({"lerobot_data_owned": True} if lerobot_owned else {}),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return 0

    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps(
            {
                "total_frames": total_frames,
                "jsonl_mtime": jsonl_mtime,
                "episodes_tasks_list": True,
                "episode_display_v4": True,
                "episodes_count": episodes_key,
                **({"lerobot_data_owned": True} if lerobot_owned else {}),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if not meta_only:
        write_meta_stats_json(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
