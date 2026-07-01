#!/usr/bin/env python3
"""Rebuild v3.0 meta/data parquet artifacts from live stream jsonl (viewer compatibility)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

import re
from datetime import datetime, timezone

PIPELINE_VERSION = "1.0.0"

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


def format_episode_list_task(ep: dict, full_task: str) -> str:
    """LeRobot sidebar line 3: {task} · {frames}f"""
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
            json.dumps(
                {
                    "task_index": 0,
                    "task": format_episode_display_task(full_task, 0),
                },
                ensure_ascii=False,
            )
        )
    (meta / "tasks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def _episode_row(ep: dict, fps: float, full_task: str, meta_defaults: dict) -> dict:
    length = int(ep.get("length") or 0)
    from_idx = int(ep.get("dataset_from_index") or 0)
    to_idx = int(ep.get("dataset_to_index") or from_idx + length)
    if length <= 0:
        length = max(0, to_idx - from_idx)
    duration = length / fps if fps > 0 else 0.0
    task = format_episode_list_task(ep, full_task)
    ep_index = int(ep.get("episode_index", 0))
    ep_meta = episode_meta_for_index_entry(ep, meta_defaults)
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
    for key in EPISODE_META_STRING_COLS:
        row[key] = ep_meta.get(key, EPISODE_META_DEFAULTS[key])
    for key in EPISODE_META_FLOAT_COLS:
        val = ep_meta.get(key)
        row[key] = float(val) if val is not None else float("nan")
    for key in VIDEO_KEYS:
        row[f"videos/{key}/chunk_index"] = 0.0
        row[f"videos/{key}/file_index"] = 0.0
        row[f"videos/{key}/from_timestamp"] = float(from_idx) / fps if fps > 0 else 0.0
        row[f"videos/{key}/to_timestamp"] = float(to_idx) / fps if fps > 0 else duration
    row["tasks"] = task
    row["_duration"] = duration
    return row


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

    rows = [_episode_row(ep, fps, task, meta_defaults) for ep in episodes]
    columns: dict[str, pa.Array] = {}
    for key in rows[0]:
        if key == "tasks":
            continue
        if key == "_duration":
            continue
        if key in EPISODE_META_STRING_COLS:
            columns[key] = pa.array([row[key] for row in rows], type=pa.string())
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
    station_id = root.name
    task = resolve_task_name(
        explicit=str(live.get("task") or "").strip() or None,
        station_id=station_id,
        session_id=str(live.get("sessionId") or ""),
        created_at=live.get("startedAt"),
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

    write_tasks_jsonl(root, episodes, task)
    ensure_annotations_skeleton(root)
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
