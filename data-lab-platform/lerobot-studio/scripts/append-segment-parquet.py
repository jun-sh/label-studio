#!/usr/bin/env python3
"""
Official LeRobot-only segment derive: one extracted tar.zst dir -> one episode.

Usage:
  append-segment-parquet.py <station_root> <extract_dir>

Requires: lerobot (see requirements-parquet-lerobot.txt). No legacy fallback.
"""

from __future__ import annotations

import io
import json
import struct
import sys
from pathlib import Path

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


def append_segment(root: Path, extract_dir: Path) -> dict:
    root = root.resolve()
    extract_dir = extract_dir.resolve()
    manifest_path = extract_dir / "manifest.json"
    rows_path = extract_dir / "rows.jsonl"
    if not manifest_path.is_file() or not rows_path.is_file():
        raise ValueError("extract_dir must contain manifest.json and rows.jsonl")

    manifest = read_json(manifest_path)
    session_id = str(manifest.get("sessionId") or manifest.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("manifest.sessionId or manifest.session_id required")

    rows = sorted(read_jsonl(rows_path), key=lambda r: int(r.get("frame_index", 0)))
    if not rows:
        raise ValueError(f"no rows in {rows_path}")

    task = episode_title_for_session(root, session_id, manifest)
    frames_dir = extract_dir / "frames"

    dataset = open_lerobot_dataset(root)
    features = dataset.features
    scalar_keys = scalar_feature_keys(features)
    video_keys = video_feature_keys(features)
    episodes_before = int(getattr(getattr(dataset, "meta", None), "total_episodes", 0) or 0)
    rows_before = int(getattr(getattr(dataset, "meta", None), "total_frames", 0) or 0)

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

    squeeze_unit_vector_buffers(dataset, features)
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
        "task": task,
        "episodes_before": episodes_before,
        "episodes_after": total_episodes,
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: append-segment-parquet.py <station_root> <extract_dir>",
            file=sys.stderr,
        )
        return 1
    root = Path(sys.argv[1])
    extract_dir = Path(sys.argv[2])
    try:
        result = append_segment(root, extract_dir)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc), "backend": "lerobot"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
