#!/usr/bin/env python3
"""
Official LeRobot-only segment derive.

Per segment (default): extracted tar.zst dir -> add_frame rows.
With --defer-save: accumulate frames in a persisted episode buffer (one session).
With --finalize-session <session_id>: save_episode once for the accumulated buffer.

Usage:
  append-segment-parquet.py <station_root> <extract_dir> [--defer-save]
  append-segment-parquet.py <station_root> --finalize-session <session_id>
"""

from __future__ import annotations

import io
import json
import struct
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

_FRAME_HEADER = struct.Struct("<4sBB")
_ENTRY_HEADER = struct.Struct("<H I")
FRAME_BIN_MAGIC = b"DLB1"


def require_lerobot_dataset():
    try:
        from lerobot.datasets.lerobot_dataset import LeRobotDataset

        return LeRobotDataset
    except ImportError as exc:
        raise RuntimeError(
            "lerobot package is required (DERIVE_PARQUET_BACKEND=lerobot). "
            "Rebuild image with requirements-parquet-lerobot.txt"
        ) from exc


def read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def unpack_frame_bin(data: bytes) -> dict[str, bytes]:
    if len(data) < _FRAME_HEADER.size:
        raise ValueError("frame bin too short")
    magic, version, n_keys = _FRAME_HEADER.unpack_from(data, 0)
    if magic != FRAME_BIN_MAGIC:
        raise ValueError(f"bad frame bin magic: {magic!r}")
    if version != 1:
        raise ValueError(f"unsupported frame bin version: {version}")
    offset = _FRAME_HEADER.size
    out: dict[str, bytes] = {}
    for _ in range(n_keys):
        key_len, jpeg_len = _ENTRY_HEADER.unpack_from(data, offset)
        offset += _ENTRY_HEADER.size
        key = data[offset : offset + key_len].decode("utf-8")
        offset += key_len
        jpeg = data[offset : offset + jpeg_len]
        offset += jpeg_len
        if jpeg:
            out[key] = jpeg
    return out


def jpeg_to_numpy(jpeg: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(jpeg))
    if img.mode != "RGB":
        img = img.convert("RGB")
    return np.asarray(img)


def default_feature_keys() -> set[str]:
    from lerobot.datasets.utils import DEFAULT_FEATURES

    return set(DEFAULT_FEATURES)


def normalize_feature_shapes(features: dict) -> dict:
    """LeRobot create() keeps list shapes from JSON; validate_frame expects tuple shapes."""
    out: dict = {}
    for key, spec in features.items():
        if not isinstance(spec, dict):
            out[key] = spec
            continue
        normalized = dict(spec)
        shape = normalized.get("shape")
        if shape is not None:
            normalized["shape"] = tuple(shape)
        out[key] = normalized
    return out


def scalar_feature_keys(features: dict) -> list[str]:
    skip = default_feature_keys()
    return [
        k
        for k, v in features.items()
        if k not in skip
        and isinstance(v, dict)
        and v.get("dtype") not in ("video", "image")
    ]


def video_feature_keys(features: dict) -> list[str]:
    return [k for k, v in features.items() if isinstance(v, dict) and v.get("dtype") == "video"]


def feature_value(features: dict, key: str, row: dict) -> np.ndarray:
    feat = features[key]
    dtype = np.dtype(feat["dtype"])
    shape = tuple(feat.get("shape") or (1,))
    if key in row and row[key] is not None:
        return np.asarray(row[key], dtype=dtype).reshape(shape)
    return np.zeros(shape, dtype=dtype)


def squeeze_unit_vector_buffers(dataset, features: dict) -> None:
    """LeRobot Value features use shape (1,) in metadata but np.stack needs 0-d scalars."""
    buf = dataset.episode_buffer
    if not buf:
        return
    for key in scalar_feature_keys(features):
        shape = tuple(features[key].get("shape") or (1,))
        if shape != (1,) or key not in buf:
            continue
        dtype = np.dtype(features[key]["dtype"])
        buf[key] = [np.asarray(value, dtype=dtype).reshape(()) for value in buf[key]]


LEGACY_PLACEHOLDER_TASK = (
    "Perform egocentric manipulation tasks at the laboratory workbench"
)


def station_short_name(station_id: str) -> str:
    import re

    sid = str(station_id or "").strip().lower()
    lan = re.match(r"ego-lan-(\d+)$", sid)
    if lan:
        return f"EGO-{lan.group(1)}"
    if sid.startswith("ego-"):
        parts = [p for p in sid[4:].split("-") if p]
        if parts and parts[-1].isdigit():
            return f"EGO-{parts[-1]}"
    return sid.upper().replace("_", "-") if sid else "EGO"


def session_id_short(session_id: str) -> str:
    token = str(session_id or "").strip()
    if token.lower().startswith("sess_"):
        token = token[5:]
    return token[:8].lower()


def auto_task_name(station_id: str, session_id: str, created_at=None) -> str:
    from datetime import datetime, timezone

    dt = created_at
    if isinstance(dt, str) and dt.strip():
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    elif not isinstance(dt, datetime):
        dt = datetime.now(timezone.utc)
    mm = f"{dt.month:02d}"
    dd = f"{dt.day:02d}"
    return f"{station_short_name(station_id)} · {session_id_short(session_id)} · {mm}-{dd}"


def episode_title_for_session(
    root: Path, session_id: str, manifest: dict | None = None
) -> str:
    """Title from manifest session_id only — never live/session.json."""
    index = read_json(root / "live" / "episodes-index.json")
    for ep in index.get("episodes") or []:
        if ep.get("session_id") == session_id:
            title = str(ep.get("title") or "").strip()
            if title:
                return title
    created_at = None
    if manifest:
        created_at = (
            manifest.get("startedAt")
            or manifest.get("created_at")
            or manifest.get("committed_at")
        )
    return auto_task_name(root.name, session_id, created_at)


def pending_episode_path(root: Path, session_id: str) -> Path:
    return root / "live" / "derive" / "pending_episode" / f"{session_id}.json"


def _jsonify_buffer_value(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    return value


def serialize_episode_buffer(buf: dict) -> dict:
    out: dict[str, Any] = {
        "size": int(buf.get("size") or 0),
        "task": list(buf.get("task") or []),
        "frame_index": list(buf.get("frame_index") or []),
        "timestamp": list(buf.get("timestamp") or []),
    }
    episode_index = buf.get("episode_index")
    if isinstance(episode_index, np.ndarray):
        out["episode_index"] = int(episode_index.reshape(-1)[0])
    else:
        out["episode_index"] = int(episode_index)
    for key, value in buf.items():
        if key in out:
            continue
        if isinstance(value, list):
            out[key] = [_jsonify_buffer_value(item) for item in value]
        else:
            out[key] = _jsonify_buffer_value(value)
    return out


def _rewrite_image_path(item: str, root: Path) -> str:
    if not isinstance(item, str):
        return item
    marker = "/images/"
    pos = item.find(marker)
    if pos >= 0:
        return f"{root.resolve()}{item[pos:]}"
    if item.startswith("/srv/stream/"):
        suffix = item.split("/srv/stream/", 1)[-1]
        station = root.name
        while suffix.startswith(f"{station}/"):
            suffix = suffix[len(station) + 1 :]
        return f"{root.resolve()}/{suffix}"
    return item


def restore_episode_buffer(dataset, payload: dict, *, root: Path | None = None) -> None:
    buf: dict[str, Any] = {
        "size": int(payload.get("size") or 0),
        "task": list(payload.get("task") or []),
        "frame_index": list(payload.get("frame_index") or []),
        "timestamp": list(payload.get("timestamp") or []),
        "episode_index": int(payload.get("episode_index") or 0),
    }
    for key, value in payload.items():
        if key in buf:
            continue
        if isinstance(value, list):
            if root is not None and key.startswith("observation.images."):
                buf[key] = [_rewrite_image_path(item, root) for item in value]
            else:
                buf[key] = list(value)
        else:
            buf[key] = value
    dataset.episode_buffer = buf


def load_pending_episode(root: Path, session_id: str) -> dict | None:
    path = pending_episode_path(root, session_id)
    if not path.is_file():
        return None
    state = read_json(path)
    if str(state.get("session_id") or "") != session_id:
        raise ValueError(f"pending episode session mismatch for {session_id}")
    return state


def save_pending_episode(
    root: Path,
    session_id: str,
    *,
    dataset,
    task: str,
    session_dataset_from_index: int,
    segment_id: str,
) -> None:
    if dataset.episode_buffer is None:
        raise ValueError("cannot persist empty episode buffer")
    write_json_atomic(
        pending_episode_path(root, session_id),
        {
            "session_id": session_id,
            "task": task,
            "session_dataset_from_index": int(session_dataset_from_index),
            "segment_ids": sorted(
                set((load_pending_episode(root, session_id) or {}).get("segment_ids") or [])
                | {segment_id}
            ),
            "buffer": serialize_episode_buffer(dataset.episode_buffer),
            "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        },
    )


def clear_pending_episode(root: Path, session_id: str) -> None:
    path = pending_episode_path(root, session_id)
    if path.is_file():
        path.unlink()


def reconcile_info_episode_count(root: Path, info: dict) -> dict:
    """Align info totals with on-disk LeRobot parquet.

    episodes-index may register a session before finalize saves its parquet row;
    LeRobotDataset then tries to load missing episodes from HuggingFace Hub.
    """
    import pyarrow.parquet as pq

    changed = False
    info = dict(info)

    def _sum_parquet_rows(directory: Path, pattern: str = "file-*.parquet") -> int:
        if not directory.is_dir():
            return 0
        total = 0
        for path in sorted(directory.glob(pattern)):
            if path.is_file() and path.stat().st_size > 100:
                total += int(pq.read_metadata(path).num_rows or 0)
        return total

    on_disk_eps = _sum_parquet_rows(root / "meta" / "episodes" / "chunk-000")
    if on_disk_eps:
        claimed_eps = int(info.get("total_episodes") or 0)
        if claimed_eps != on_disk_eps:
            info["total_episodes"] = on_disk_eps
            changed = True

    on_disk_frames = _sum_parquet_rows(root / "data" / "chunk-000")
    if on_disk_frames:
        claimed_frames = int(info.get("total_frames") or 0)
        if claimed_frames != on_disk_frames:
            info["total_frames"] = on_disk_frames
            changed = True

    if changed:
        write_json_atomic(root / "meta" / "info.json", info)
    return info


def _episode_index_for_session(root: Path, session_id: str) -> int | None:
    idx_path = root / "live" / "episodes-index.json"
    if not idx_path.is_file():
        return None
    for ep in read_json(idx_path).get("episodes") or []:
        if str(ep.get("session_id") or "") == session_id:
            return int(ep.get("episode_index", 0))
    return None


def open_lerobot_dataset(root: Path):
    import contextlib
    import pathlib
    import shutil

    LeRobotDataset = require_lerobot_dataset()
    info_path = root / "meta" / "info.json"
    if not info_path.is_file():
        raise ValueError(f"missing meta/info.json: {info_path}")
    info = read_json(info_path)
    data_parquet = root / "data" / "chunk-000" / "file-000.parquet"
    has_data = data_parquet.is_file() and data_parquet.stat().st_size > 100

    if has_data:
        info = reconcile_info_episode_count(root, info)
        write_tasks_parquet_if_missing(root, info)
        episodes_pq = root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
        if episodes_pq.is_file():
            import pyarrow.parquet as pq

            if pq.read_metadata(episodes_pq).num_rows == 0:
                episodes_pq.unlink()
        return LeRobotDataset(
            repo_id=root.name,
            root=root,
            download_videos=False,
            force_cache_sync=False,
        )

    for rel in ("meta/episodes", "meta/tasks.parquet", "data", "videos"):
        target = root / rel
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()

    @contextlib.contextmanager
    def allow_existing_root():
        orig = pathlib.Path.mkdir

        def patched(self, *args, **kwargs):
            if self == root:
                kwargs["exist_ok"] = True
            return orig(self, *args, **kwargs)

        pathlib.Path.mkdir = patched
        try:
            yield
        finally:
            pathlib.Path.mkdir = orig

    with allow_existing_root():
        return LeRobotDataset.create(
            repo_id=root.name,
            root=root,
            fps=int(info.get("fps") or 30),
            robot_type=str(info.get("robot_type") or "oak_4p_ego"),
            features=normalize_feature_shapes(info.get("features") or {}),
            use_videos=True,
        )


def write_tasks_parquet_if_missing(root: Path, info: dict) -> None:
    tasks_pq = root / "meta" / "tasks.parquet"
    if tasks_pq.is_file():
        return
    import pyarrow as pa
    import pyarrow.parquet as pq

    label = str(info.get("robot_type") or root.name)
    table = pa.Table.from_pylist([{"task_index": 0, "task": label}])
    tasks_pq.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, tasks_pq)


def _episode_index_value(dataset) -> int:
    buf = dataset.episode_buffer or {}
    episode_index = buf.get("episode_index", 0)
    if isinstance(episode_index, np.ndarray):
        return int(episode_index.reshape(-1)[0])
    return int(episode_index)


def _ingest_segment_rows(dataset, rows: list[dict], frames_dir: Path, features: dict, task: str) -> int:
    scalar_keys = scalar_feature_keys(features)
    video_keys = video_feature_keys(features)
    for row in rows:
        local_index = int(row.get("frame_index", 0))
        bin_path = frames_dir / f"{local_index:08d}.bin"
        cameras = unpack_frame_bin(bin_path.read_bytes()) if bin_path.is_file() else {}

        frame: dict = {"task": task}
        for key in scalar_keys:
            frame[key] = feature_value(features, key, row)
        for vkey in video_keys:
            jpeg = cameras.get(vkey)
            if jpeg:
                frame[vkey] = jpeg_to_numpy(jpeg)
        dataset.add_frame(frame)
    return len(rows)


def append_segment(root: Path, extract_dir: Path, *, defer_save: bool = False) -> dict:
    root = root.resolve()
    extract_dir = extract_dir.resolve()
    manifest_path = extract_dir / "manifest.json"
    rows_path = extract_dir / "rows.jsonl"
    if not manifest_path.is_file() or not rows_path.is_file():
        raise ValueError("extract_dir must contain manifest.json and rows.jsonl")

    manifest = read_json(manifest_path)
    session_id = str(manifest.get("sessionId") or manifest.get("session_id") or "").strip()
    segment_id = str(manifest.get("segmentId") or manifest.get("segment_id") or "").strip()
    if not session_id:
        raise ValueError("manifest.sessionId or manifest.session_id required")

    rows = sorted(read_jsonl(rows_path), key=lambda r: int(r.get("frame_index", 0)))
    if not rows:
        raise ValueError(f"no rows in {rows_path}")

    task = episode_title_for_session(root, session_id, manifest)
    frames_dir = extract_dir / "frames"

    dataset = open_lerobot_dataset(root)
    features = dataset.features
    episodes_before = int(getattr(getattr(dataset, "meta", None), "total_episodes", 0) or 0)
    rows_before = int(getattr(getattr(dataset, "meta", None), "total_frames", 0) or 0)

    pending = load_pending_episode(root, session_id) if defer_save else None
    if pending:
        restore_episode_buffer(dataset, pending["buffer"], root=root)
        session_dataset_from_index = int(pending.get("session_dataset_from_index") or rows_before)
    else:
        session_dataset_from_index = rows_before
        if defer_save:
            ep_idx = _episode_index_for_session(root, session_id)
            if ep_idx is not None and dataset.episode_buffer is not None:
                dataset.episode_buffer["episode_index"] = ep_idx

    rows_added = _ingest_segment_rows(dataset, rows, frames_dir, features, task)
    squeeze_unit_vector_buffers(dataset, features)

    if defer_save:
        save_pending_episode(
            root,
            session_id,
            dataset=dataset,
            task=task,
            session_dataset_from_index=session_dataset_from_index,
            segment_id=segment_id or "unknown",
        )
        pending_size = int(dataset.episode_buffer.get("size") or 0)
        return {
            "backend": "lerobot",
            "rows_added": rows_added,
            "total_rows": rows_before,
            "parquet_rows": rows_before,
            "frames_committed": rows_added,
            "episode_index": _episode_index_value(dataset),
            "dataset_from_index": session_dataset_from_index,
            "dataset_to_index": session_dataset_from_index + pending_size,
            "session_id": session_id,
            "segment_id": segment_id,
            "task": task,
            "episodes_before": episodes_before,
            "episodes_after": episodes_before,
            "episode_saved": False,
            "pending_frames": pending_size,
            "defer_save": True,
        }

    dataset.save_episode()

    meta = getattr(dataset, "meta", None)
    total_rows = int(getattr(meta, "total_frames", 0) or 0)
    total_episodes = int(getattr(meta, "total_episodes", 0) or 0)
    episode_index = max(0, total_episodes - 1)
    length = len(rows)

    if hasattr(dataset, "validate"):
        dataset.validate()

    return {
        "backend": "lerobot",
        "rows_added": length,
        "total_rows": total_rows,
        "parquet_rows": total_rows,
        "frames_committed": length,
        "episode_index": episode_index,
        "dataset_from_index": rows_before,
        "dataset_to_index": rows_before + length,
        "session_id": session_id,
        "segment_id": segment_id,
        "task": task,
        "episodes_before": episodes_before,
        "episodes_after": total_episodes,
        "episode_saved": True,
        "defer_save": False,
    }


def finalize_session_episode(root: Path, session_id: str) -> dict:
    root = root.resolve()
    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("session_id required")

    pending = load_pending_episode(root, session_id)
    if not pending:
        return {
            "backend": "lerobot",
            "skipped": True,
            "reason": "no_pending_episode",
            "session_id": session_id,
        }

    dataset = open_lerobot_dataset(root)
    restore_episode_buffer(dataset, pending["buffer"], root=root)
    features = dataset.features
    squeeze_unit_vector_buffers(dataset, features)

    ep_idx = _episode_index_for_session(root, session_id)
    if ep_idx is not None and dataset.episode_buffer is not None:
        dataset.episode_buffer["episode_index"] = ep_idx

    episodes_before = int(getattr(getattr(dataset, "meta", None), "total_episodes", 0) or 0)
    rows_before = int(getattr(getattr(dataset, "meta", None), "total_frames", 0) or 0)
    pending_size = int(dataset.episode_buffer.get("size") or 0)
    session_dataset_from_index = int(pending.get("session_dataset_from_index") or rows_before)
    task = str(pending.get("task") or "")

    dataset.save_episode()
    clear_pending_episode(root, session_id)

    meta = getattr(dataset, "meta", None)
    total_rows = int(getattr(meta, "total_frames", 0) or 0)
    total_episodes = int(getattr(meta, "total_episodes", 0) or 0)
    episode_index = max(0, total_episodes - 1)

    if hasattr(dataset, "validate"):
        dataset.validate()

    return {
        "backend": "lerobot",
        "skipped": False,
        "session_id": session_id,
        "episode_saved": True,
        "rows_added": pending_size,
        "total_rows": total_rows,
        "parquet_rows": total_rows,
        "frames_committed": pending_size,
        "episode_index": episode_index,
        "dataset_from_index": session_dataset_from_index,
        "dataset_to_index": session_dataset_from_index + pending_size,
        "task": task,
        "episodes_before": episodes_before,
        "episodes_after": total_episodes,
        "segment_ids": pending.get("segment_ids") or [],
    }


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) < 2:
        print(
            "usage: append-segment-parquet.py <station_root> <extract_dir> [--defer-save]\n"
            "       append-segment-parquet.py <station_root> --finalize-session <session_id>",
            file=sys.stderr,
        )
        return 1

    root = Path(argv[0])
    if argv[1] == "--finalize-session":
        if len(argv) < 3:
            print("missing session_id for --finalize-session", file=sys.stderr)
            return 1
        try:
            result = finalize_session_episode(root, argv[2])
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except Exception as exc:
            print(json.dumps({"error": str(exc), "backend": "lerobot"}), file=sys.stderr)
            return 1

    extract_dir = Path(argv[1])
    defer_save = "--defer-save" in argv[2:]
    try:
        result = append_segment(root, extract_dir, defer_save=defer_save)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc), "backend": "lerobot"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
