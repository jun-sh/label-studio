"""LeRobot v3.0 dataset loading — read-only, no embodied annotation logic."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException


ALLOWED_CODEBASE_VERSION = "v3.0"


@dataclass
class DatasetState:
    dataset_root: Path
    info: dict[str, Any]
    episodes_df: pd.DataFrame
    tasks_map: dict[int, str] = field(default_factory=dict)
    video_key: str | None = None

    @property
    def fps(self) -> float:
        return float(self.info.get("fps") or 30)

    def video_keys(self) -> list[str]:
        features = self.info.get("features") or {}
        return sorted(k for k, spec in features.items() if spec.get("dtype") == "video")

    def resolve_video_key(self, video_key: str | None) -> str:
        selected = video_key or self.video_key
        if not selected:
            raise HTTPException(status_code=400, detail="video_key is required")

        keys = self.video_keys()
        if selected in keys:
            return selected

        suffix_matches = [k for k in keys if k.endswith(f".{selected}") or k.endswith(f"/{selected}")]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        if len(suffix_matches) > 1:
            exact = [k for k in suffix_matches if k.split(".")[-1] == selected]
            if len(exact) == 1:
                return exact[0]

        raise HTTPException(
            status_code=400,
            detail=f"Video key '{selected}' not available",
        )

    def scalar_feature_keys(self) -> list[str]:
        features = self.info.get("features") or {}
        keys: list[str] = []
        for key, spec in features.items():
            if spec.get("dtype") == "video":
                continue
            keys.append(key)
        return sorted(keys)

    def episode_row(self, episode_index: int) -> pd.Series:
        row = self.episodes_df[self.episodes_df["episode_index"] == episode_index]
        if row.empty:
            raise HTTPException(status_code=404, detail=f"Episode {episode_index} not found")
        return row.iloc[0]

    def resolve_language_instruction(self, row: pd.Series) -> tuple[str, int | None]:
        if "tasks" in row.index:
            tasks_val = row["tasks"]
            if tasks_val is not None and not (isinstance(tasks_val, float) and pd.isna(tasks_val)):
                if isinstance(tasks_val, str) and tasks_val.strip():
                    return tasks_val.strip(), None
                if hasattr(tasks_val, "__len__") and not isinstance(tasks_val, str):
                    try:
                        items = list(tasks_val)
                    except TypeError:
                        items = []
                    if items:
                        text = str(items[0]).strip()
                        if text:
                            return text, None

        task_index: int | None = None
        if "task_index" in row.index:
            ti = row["task_index"]
            if pd.notna(ti):
                task_index = int(ti)
                if task_index in self.tasks_map:
                    return self.tasks_map[task_index], task_index

        if len(self.tasks_map) == 1:
            only_idx = next(iter(self.tasks_map))
            return self.tasks_map[only_idx], only_idx

        return "", task_index

    def episode_length(self, row: pd.Series) -> int:
        if "length" in row.index and pd.notna(row["length"]):
            return int(row["length"])
        from_idx = int(row.get("dataset_from_index", 0) or 0)
        to_idx = int(row.get("dataset_to_index", 0) or 0)
        return max(0, to_idx - from_idx)

    def video_offsets(self, video_key: str, episode_index: int) -> dict[str, float]:
        video_key = self.resolve_video_key(video_key)
        row = self.episode_row(episode_index)
        length = self.episode_length(row)
        duration = length / self.fps if self.fps else 0.0
        from_col = f"videos/{video_key}/from_timestamp"
        to_col = f"videos/{video_key}/to_timestamp"
        if from_col in row.index and to_col in row.index:
            from_ts = row.get(from_col)
            to_ts = row.get(to_col)
            if pd.notna(from_ts) and pd.notna(to_ts):
                return {
                    "video_start_time": float(from_ts),
                    "video_end_time": float(to_ts),
                }
        return {"video_start_time": 0.0, "video_end_time": duration}

    def video_path(self, episode_index: int, video_key: str | None = None) -> Path:
        video_key = self.resolve_video_key(video_key)

        row = self.episode_row(episode_index)
        chunk_col = f"videos/{video_key}/chunk_index"
        file_col = f"videos/{video_key}/file_index"
        if chunk_col not in row.index or file_col not in row.index:
            raise HTTPException(status_code=400, detail=f"Video key '{video_key}' not available")

        chunk_index = int(row[chunk_col])
        file_index = int(row[file_col])
        features = self.info.get("features") or {}
        feature_info = (features.get(video_key) or {}).get("info") or {}
        rel_tpl = (
            feature_info.get("depth.video_path")
            or self.info.get("video_path")
            or "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
        )
        rel_path = rel_tpl.format(
            video_key=video_key,
            chunk_index=chunk_index,
            file_index=file_index,
        )
        full_path = (self.dataset_root / rel_path).resolve()
        if full_path.is_file():
            return full_path
        # Fallback: alternate extension when template does not match on-disk container.
        alt_suffix = ".mkv" if full_path.suffix.lower() == ".mp4" else ".mp4"
        alt_path = full_path.with_suffix(alt_suffix)
        if alt_path.is_file():
            return alt_path
        raise HTTPException(status_code=404, detail=f"Video file not found: {full_path}")

    def frame_bounds(self, episode_index: int) -> tuple[int, int]:
        row = self.episode_row(episode_index)
        if "dataset_from_index" in row.index and "dataset_to_index" in row.index:
            start = int(row["dataset_from_index"])
            end = int(row["dataset_to_index"])
            return start, end
        length = self.episode_length(row)
        return 0, length

    def episode_preview(self, row: pd.Series) -> dict[str, Any]:
        length = self.episode_length(row)
        duration = length / self.fps if self.fps else 0.0
        return {
            "episode_index": int(row["episode_index"]),
            "length": length,
            "duration": duration,
        }

    def build_summary(
        self,
        visible_episode_indices: list[int] | None = None,
        *,
        include_episodes: bool = True,
    ) -> dict[str, Any]:
        video_keys = self.video_keys()
        selected = self.video_key or (video_keys[0] if video_keys else None)
        episodes: list[dict[str, Any]] = []

        if not include_episodes:
            return {
                "root": str(self.dataset_root),
                "codebase_version": self.info.get("codebase_version"),
                "robot_type": self.info.get("robot_type"),
                "fps": self.fps,
                "chunks_size": self.info.get("chunks_size", 1000),
                "total_episodes": int(self.info.get("total_episodes") or len(self.episodes_df)),
                "total_frames": int(self.info.get("total_frames") or 0),
                "video_keys": video_keys,
                "selected_video_key": selected,
                "scalar_features": self.scalar_feature_keys(),
                "episodes": [],
                "vendor_meta": self.info.get("vendor_meta"),
                "ego_capture": self.info.get("ego_capture"),
            }

        for _, row in self.episodes_df.iterrows():
            ep_idx = int(row["episode_index"])
            if visible_episode_indices is not None and ep_idx not in visible_episode_indices:
                continue
            length = self.episode_length(row)
            duration = length / self.fps if self.fps else 0.0
            instruction, task_index = self.resolve_language_instruction(row)
            offsets = self.video_offsets(selected, ep_idx) if selected else {}
            entry: dict[str, Any] = {
                "episode_index": ep_idx,
                "length": length,
                "duration": duration,
                "language_instruction": instruction,
                **offsets,
            }
            if task_index is not None:
                entry["task_index"] = task_index
            episodes.append(entry)

        return {
            "root": str(self.dataset_root),
            "codebase_version": self.info.get("codebase_version"),
            "robot_type": self.info.get("robot_type"),
            "fps": self.fps,
            "chunks_size": self.info.get("chunks_size", 1000),
            "total_episodes": int(self.info.get("total_episodes") or len(self.episodes_df)),
            "total_frames": int(self.info.get("total_frames") or 0),
            "video_keys": video_keys,
            "selected_video_key": selected,
            "scalar_features": self.scalar_feature_keys(),
            "episodes": episodes,
            "vendor_meta": self.info.get("vendor_meta"),
            "ego_capture": self.info.get("ego_capture"),
        }


def _load_info(root: Path) -> dict[str, Any]:
    info_path = root / "meta" / "info.json"
    if not info_path.is_file():
        raise HTTPException(status_code=404, detail=f"Missing info.json at {info_path}")
    return json.loads(info_path.read_text(encoding="utf-8"))


def _load_episodes(root: Path) -> pd.DataFrame:
    episodes_root = root / "meta" / "episodes"
    if not episodes_root.is_dir():
        raise HTTPException(status_code=404, detail=f"Missing episodes directory at {episodes_root}")
    files = sorted(episodes_root.rglob("*.parquet"))
    if not files:
        raise HTTPException(status_code=404, detail="No episodes parquet files found")
    df = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
    if "episode_index" not in df.columns:
        raise HTTPException(status_code=400, detail="episodes parquet missing episode_index column")
    return df.sort_values("episode_index").reset_index(drop=True)


def _load_tasks_map(root: Path) -> dict[int, str]:
    tasks_map: dict[int, str] = {}
    tasks_parquet = root / "meta" / "tasks.parquet"
    if not tasks_parquet.is_file():
        return tasks_map

    df = pd.read_parquet(tasks_parquet)
    if "task" in df.columns:
        raise ValueError(
            f"legacy tasks.parquet at {tasks_parquet}: task text must be the DataFrame index"
        )
    if "task_index" not in df.columns:
        raise ValueError(f"tasks.parquet missing task_index column: {tasks_parquet}")
    for task_text, row in df.iterrows():
        tasks_map[int(row["task_index"])] = str(task_text)
    return tasks_map


def validate_v3_dataset(root: Path) -> None:
    info = _load_info(root)
    version = str(info.get("codebase_version") or "").strip()
    if version != ALLOWED_CODEBASE_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Only LeRobot {ALLOWED_CODEBASE_VERSION} datasets are supported (got {version!r})",
        )


def _default_video_key(keys: list[str], info: dict[str, Any]) -> str:
    features = info.get("features") or {}
    rgb_keys: list[str] = []
    for key in keys:
        meta = (features.get(key) or {}).get("info") or {}
        if meta.get("video.is_depth_map"):
            continue
        if "depth" in key.lower():
            continue
        rgb_keys.append(key)
    return rgb_keys[0] if rgb_keys else keys[0]


def load_local_dataset(local_path: str, video_key: str | None = None) -> DatasetState:
    root = Path(local_path).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Dataset path not found: {root}")

    validate_v3_dataset(root)
    info = _load_info(root)
    episodes_df = _load_episodes(root)
    tasks_map = _load_tasks_map(root)
    keys = sorted(
        k for k, spec in (info.get("features") or {}).items() if spec.get("dtype") == "video"
    )
    if not keys:
        raise HTTPException(status_code=400, detail="Dataset has no video keys")

    selected = video_key or _default_video_key(keys, info)
    if selected not in keys:
        raise HTTPException(
            status_code=400,
            detail=f"Video key '{selected}' not found. Available: {', '.join(keys)}",
        )

    return DatasetState(
        dataset_root=root,
        info=info,
        episodes_df=episodes_df,
        tasks_map=tasks_map,
        video_key=selected,
    )
