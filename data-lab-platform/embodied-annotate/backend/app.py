import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from huggingface_hub import HfApi, hf_hub_download, snapshot_download
from pydantic import BaseModel, Field

import ego_parquet
from annotation_schema import (
    resolve_annotation_schema,
    schema_ref,
    skill_derivation_enabled,
    filter_episode_fields,
    validate_episode_fields,
    validate_skill_cycles,
    validate_subtasks,
)
from annotation_job import AnnotationJob
from annotation_jobs_service import (
    auto_select_open_job,
    build_job_detail,
    compute_job_episode_indices,
    filter_jobs,
    get_job_by_id,
    infer_collection_context,
    job_schema_ref,
    job_to_summary,
    load_collection_jobs,
    load_jobs_document,
    package_requires_job,
    resolve_job_for_load,
)
from task_family import (
    JobFamilyMismatchError,
    TaskFamilyUnresolvedError,
    load_schema_by_id,
    load_task_family_map,
    resolve_schema_for_episode,
)
from dataset_catalog import datasets_root, get_collection, list_collections, resolve_package_path
from episode_progress import (
    build_annotation_progress,
    episode_annotation_status,
    soft_validate_episode,
)
from export_builders import (
    build_cycles_dataframe,
    build_episodes_meta_dataframe,
    build_export_manifest,
    build_skill_segments_dataframe,
    build_subtasks_lookup_dataframe,
)
from export_staging import stage_training_meta
from skill_derivation import apply_skill_derivation, sync_box_cycle_from_cycles
from ego_parquet import (
    episode_meta_public,
    episode_row,
    episode_total_frames,
    load_info,
    read_annotation_boundaries,
    read_hand_poses_for_episode,
    scalar_features,
    segments_from_boundaries,
    update_episode_after_subtask_save,
    write_annotations_for_episode,
)

APP_ROOT = Path(__file__).resolve().parent
STATIC_DIR = APP_ROOT / "static"
CACHE_ROOT = Path(os.environ.get("LEROBOT_ANNOTATE_CACHE", "/tmp/lerobot_annotate_cache"))
EXPORT_ROOT = Path(os.environ.get("LEROBOT_ANNOTATE_EXPORT", "/tmp/lerobot_annotate_exports"))
TRIMMED_VIDEO_CACHE = CACHE_ROOT / "trimmed_videos"


def trim_video_with_ffmpeg(input_path: Path, output_path: Path, start_time: float, end_time: float) -> bool:
    """Trim a video using FFmpeg to extract only the specified time range.
    
    Args:
        input_path: Path to the source video file
        output_path: Path where the trimmed video should be saved
        start_time: Start time in seconds
        end_time: End time in seconds
        
    Returns:
        True if successful, False otherwise
    """
    duration = end_time - start_time
    if duration <= 0:
        return False
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        # Use FFmpeg to trim the video
        # -ss before -i for fast seeking, -t for duration
        # -c copy for fast copying without re-encoding (if possible)
        # -avoid_negative_ts make_zero to handle timestamp issues
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output file if exists
            "-ss", str(start_time),  # Start time (before -i for input seeking)
            "-i", str(input_path),
            "-t", str(duration),  # Duration
            "-c", "copy",  # Copy codecs (fast, no re-encoding)
            "-avoid_negative_ts", "make_zero",
            str(output_path),
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        
        if result.returncode != 0:
            print(f"FFmpeg error: {result.stderr}")
            # Try again with re-encoding if copy fails
            cmd_reencode = [
                "ffmpeg",
                "-y",
                "-ss", str(start_time),
                "-i", str(input_path),
                "-t", str(duration),
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-c:a", "aac",
                str(output_path),
            ]
            result = subprocess.run(cmd_reencode, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                print(f"FFmpeg re-encode error: {result.stderr}")
                return False
        
        return output_path.exists()
    except subprocess.TimeoutExpired:
        print("FFmpeg timed out")
        return False
    except FileNotFoundError:
        print("FFmpeg not found - please install FFmpeg")
        return False
    except Exception as e:
        print(f"Error trimming video: {e}")
        return False


def get_trimmed_video_cache_path(video_path: Path, episode_index: int, start_time: float, end_time: float) -> Path:
    """Generate a unique cache path for a trimmed video segment."""
    # Create a hash based on the source video path and time range
    key = f"{video_path}_{episode_index}_{start_time:.3f}_{end_time:.3f}"
    hash_key = hashlib.md5(key.encode()).hexdigest()[:16]
    return TRIMMED_VIDEO_CACHE / f"ep{episode_index}_{hash_key}.mp4"


class DatasetLoadRequest(BaseModel):
    source: str  # "hf" or "local"
    repo_id: str | None = None
    revision: str | None = None
    local_path: str | None = None
    video_key: str | None = None
    collection_id: str | None = None
    package_id: str | None = None
    job_id: str | None = None


class SegmentSubtask(BaseModel):
    start: float
    end: float
    label: str


class SegmentHighLevel(BaseModel):
    start: float
    end: float
    user_prompt: str
    robot_utterance: str
    skill: str | None = None
    scenario_type: str | None = None
    response_type: str | None = None


VALID_OUTCOMES = frozenset({"success", "fail", "partial"})


class EpisodeAnnotationsPayload(BaseModel):
    episode_index: int
    subtasks: list[SegmentSubtask] = []
    high_levels: list[SegmentHighLevel] = []
    skill_cycles: list[dict[str, Any]] = Field(default_factory=list)
    outcome: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


@dataclass
class EpisodeAnnotations:
    subtasks: list[dict[str, Any]] = field(default_factory=list)
    high_levels: list[dict[str, Any]] = field(default_factory=list)
    skill_segments: list[dict[str, Any]] = field(default_factory=list)
    skill_cycles: list[dict[str, Any]] = field(default_factory=list)
    skill_derivation_warnings: list[str] = field(default_factory=list)
    outcome: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)


class DataManager:
    def __init__(self) -> None:
        self.source: str | None = None
        self.repo_id: str | None = None
        self.revision: str | None = None
        self.dataset_root: Path | None = None
        self.info: dict[str, Any] | None = None
        self.episodes_df: pd.DataFrame | None = None
        self.video_key: str | None = None
        self.tasks_map: dict[int, str] = {}
        self.annotations: dict[int, EpisodeAnnotations] = {}
        self.annotations_path: Path | None = None
        self.annotation_schema: dict[str, Any] = {}
        self.schema_ref: str | None = None
        self.collection_id: str | None = None
        self.package_id: str | None = None
        self.collection_dir: Path | None = None
        self.family_map: dict[str, Any] | None = None
        self.active_job: AnnotationJob | None = None
        self.job_episode_indices: set[int] = set()

    def _episode_row(self, episode_index: int) -> pd.Series:
        assert self.episodes_df is not None
        row = self.episodes_df[self.episodes_df["episode_index"] == episode_index]
        if row.empty:
            raise HTTPException(status_code=404, detail=f"Episode {episode_index} not found")
        return row.iloc[0]

    def _assert_episode_in_job(self, episode_index: int) -> None:
        if self.active_job is None:
            return
        if int(episode_index) not in self.job_episode_indices:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Episode {episode_index} is outside active job "
                    f"{self.active_job.job_id!r} (J-01)"
                ),
            )

    def _episode_schema_resolution(self, episode_index: int):
        row = self._episode_row(episode_index)
        task_text, task_index = self._resolve_episode_task_text(row)
        try:
            return resolve_schema_for_episode(
                collection_id=self.collection_id or "",
                package_id=self.package_id or (self.dataset_root.name if self.dataset_root else ""),
                episode_index=episode_index,
                task_text=task_text,
                task_index=task_index,
                active_job=self.active_job,
                family_map=self.family_map,
                package_root=self.dataset_root,
            )
        except JobFamilyMismatchError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def _episode_schema(self, episode_index: int) -> dict[str, Any]:
        return self._episode_schema_resolution(episode_index).schema

    def _configure_job_context(self, req: DatasetLoadRequest) -> None:
        assert self.dataset_root is not None
        inferred_collection, inferred_package = infer_collection_context(self.dataset_root)
        self.collection_id = req.collection_id or inferred_collection
        self.package_id = req.package_id or inferred_package
        self.collection_dir = None
        self.family_map = None
        self.active_job = None
        self.job_episode_indices = set()

        if not self.collection_id:
            self.annotation_schema = resolve_annotation_schema(self.dataset_root)
            self.schema_ref = schema_ref(self.annotation_schema)
            assert self.episodes_df is not None
            self.job_episode_indices = {
                int(x) for x in self.episodes_df["episode_index"].tolist()
            }
            return

        self.collection_dir = datasets_root() / self.collection_id
        if self.collection_dir.is_dir():
            self.family_map = load_task_family_map(self.collection_dir)

        jobs_doc_exists = (
            self.collection_dir.is_dir()
            and load_jobs_document(self.collection_dir) is not None
        )
        if jobs_doc_exists and self.package_id:
            try:
                self.active_job = resolve_job_for_load(
                    collection_dir=self.collection_dir,
                    package_id=self.package_id,
                    job_id=req.job_id,
                    family_map=self.family_map,
                )
            except ValueError:
                self.active_job = None
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"Job not found: {req.job_id}") from exc
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except LookupError as exc:
                open_jobs = filter_jobs(
                    load_collection_jobs(self.collection_dir),
                    package_id=self.package_id,
                    status="open",
                )
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": str(exc),
                        "requires_job": True,
                        "open_jobs": [j.job_id for j in open_jobs],
                    },
                ) from exc
            except JobFamilyMismatchError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        assert self.episodes_df is not None
        if self.active_job is not None:
            indices = compute_job_episode_indices(
                self.active_job,
                self.episodes_df,
                family_map=self.family_map,
                resolve_task_text=self._resolve_episode_task_text,
            )
            self.job_episode_indices = set(indices)
            self.annotation_schema = load_schema_by_id(
                self.active_job.schema_id,
                package_root=self.dataset_root,
            )
            self.schema_ref = schema_ref(self.annotation_schema)
        else:
            self.annotation_schema = resolve_annotation_schema(self.dataset_root)
            self.schema_ref = schema_ref(self.annotation_schema)
            self.job_episode_indices = {
                int(x) for x in self.episodes_df["episode_index"].tolist()
            }

    def _active_job_payload(self) -> dict[str, Any] | None:
        if self.active_job is None:
            return None
        return {
            "job_id": self.active_job.job_id,
            "display_name": self.active_job.display_name,
            "collection_id": self.active_job.collection_id,
            "package_id": self.active_job.package_id,
            "task_family": self.active_job.task_family,
            "schema_id": self.active_job.schema_id,
            "schema_ref": self.schema_ref,
            "scope": self.active_job.scope,
            "status": self.active_job.status,
            "episode_count": len(self.job_episode_indices),
        }

    def load_dataset(self, req: DatasetLoadRequest) -> dict[str, Any]:
        if req.source not in {"hf", "local"}:
            raise HTTPException(status_code=400, detail="source must be 'hf' or 'local'")

        self.source = req.source
        self.repo_id = req.repo_id
        self.revision = req.revision

        if req.source == "local":
            if not req.local_path:
                raise HTTPException(status_code=400, detail="local_path is required for local source")
            root = Path(req.local_path).expanduser().resolve()
            if not root.exists():
                raise HTTPException(status_code=404, detail=f"Dataset path not found: {root}")
            self.dataset_root = root
        else:
            if not req.repo_id:
                raise HTTPException(status_code=400, detail="repo_id is required for hf source")
            CACHE_ROOT.mkdir(parents=True, exist_ok=True)
            repo_dir = CACHE_ROOT / req.repo_id.replace("/", "__")
            repo_dir.mkdir(parents=True, exist_ok=True)
            snapshot_download(
                req.repo_id,
                repo_type="dataset",
                revision=req.revision,
                local_dir=repo_dir,
                allow_patterns=["meta/*"],
            )
            self.dataset_root = repo_dir

        self.info = self._load_info(self.dataset_root)
        self.episodes_df = self._load_episodes(self.dataset_root)
        self.tasks_map = self._load_tasks_map(self.dataset_root)

        video_keys = self._get_video_keys()
        if not video_keys:
            raise HTTPException(status_code=400, detail="Dataset has no video keys")
        self.video_key = req.video_key or video_keys[0]
        if self.video_key not in video_keys:
            raise HTTPException(
                status_code=400,
                detail=f"Video key '{self.video_key}' not found. Available: {', '.join(video_keys)}",
            )

        self.annotations_path = self.dataset_root / "meta" / "lerobot_annotations.json"
        self._configure_job_context(req)
        self._load_existing_annotations()
        return self._build_summary()

    def _load_info(self, root: Path) -> dict[str, Any]:
        info_path = root / "meta" / "info.json"
        if not info_path.exists():
            raise HTTPException(status_code=404, detail=f"Missing info.json at {info_path}")
        return json.loads(info_path.read_text())

    def _load_episodes(self, root: Path) -> pd.DataFrame:
        episodes_root = root / "meta" / "episodes"
        if not episodes_root.exists():
            raise HTTPException(status_code=404, detail=f"Missing episodes directory at {episodes_root}")
        files = sorted(episodes_root.rglob("*.parquet"))
        if not files:
            raise HTTPException(status_code=404, detail="No episodes parquet files found")
        dfs = [pd.read_parquet(path) for path in files]
        df = pd.concat(dfs, ignore_index=True)
        if "episode_index" not in df.columns:
            raise HTTPException(status_code=400, detail="episodes parquet missing 'episode_index' column")
        return df.sort_values("episode_index").reset_index(drop=True)

    def _load_tasks_map(self, root: Path) -> dict[int, str]:
        tasks_map: dict[int, str] = {}
        tasks_parquet = root / "meta" / "tasks.parquet"
        if tasks_parquet.exists():
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

    def _resolve_episode_task_text(self, row: pd.Series) -> tuple[str | None, int | None]:
        task_index: int | None = None

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

        if "task_index" in row.index:
            ti = row["task_index"]
            if pd.notna(ti):
                task_index = int(ti)
                if task_index in self.tasks_map:
                    return self.tasks_map[task_index], task_index

        if len(self.tasks_map) == 1:
            only_idx = next(iter(self.tasks_map))
            return self.tasks_map[only_idx], only_idx

        return None, task_index

    def _episode_payload_from_storage(self, payload: dict[str, Any]) -> EpisodeAnnotations:
        return EpisodeAnnotations(
            subtasks=payload.get("subtasks", []),
            high_levels=payload.get("high_levels", []),
            skill_segments=payload.get("skill_segments", []),
            skill_cycles=payload.get("skill_cycles", []),
            skill_derivation_warnings=payload.get("skill_derivation_warnings", []),
            outcome=payload.get("outcome"),
            fields=payload.get("fields") or {},
        )

    def _refresh_skill_derivation(self, ann: EpisodeAnnotations, episode_index: int) -> None:
        schema = self._episode_schema(episode_index)
        if not skill_derivation_enabled(schema):
            return
        previous_cycles = list(ann.skill_cycles)
        segments, cycles, warnings = apply_skill_derivation(
            ann.subtasks,
            schema,
            previous_cycles=previous_cycles,
        )
        ann.skill_segments = segments
        ann.skill_cycles = cycles
        ann.skill_derivation_warnings = warnings
        if any(spec.get("id") == "box_cycle" for spec in schema.get("episode_fields") or []):
            ann.fields = sync_box_cycle_from_cycles(ann.skill_cycles, ann.fields)

    def _maybe_reset_skill_review(
        self,
        old: EpisodeAnnotations | None,
        new_subtasks: list[dict[str, Any]],
        fields: dict[str, Any],
        episode_index: int,
    ) -> dict[str, Any]:
        fields = dict(fields or {})
        schema = self._episode_schema(episode_index)
        if not skill_derivation_enabled(schema):
            return fields
        if old and old.subtasks != new_subtasks and fields.get("skill_review") == "approved":
            fields["skill_review"] = "pending"
        if "skill_review" not in fields or fields.get("skill_review") in (None, ""):
            fields.setdefault("skill_review", "pending")
        return fields

    def _episode_status(self, ann: EpisodeAnnotations, episode_index: int) -> str:
        return episode_annotation_status(
            ann.subtasks,
            ann.outcome,
            ann.fields,
            self._episode_schema(episode_index),
        )

    def _annotation_response(self, episode_index: int, ann: EpisodeAnnotations) -> dict[str, Any]:
        resolution = self._episode_schema_resolution(episode_index)
        return {
            "episode_index": episode_index,
            "subtasks": ann.subtasks,
            "high_levels": ann.high_levels,
            "skill_segments": ann.skill_segments,
            "skill_cycles": ann.skill_cycles,
            "skill_derivation_warnings": ann.skill_derivation_warnings,
            "outcome": ann.outcome,
            "fields": ann.fields,
            "annotation_status": self._episode_status(ann, episode_index),
            "task_family": resolution.task_family,
            "schema_ref": resolution.schema_ref,
            "l2_annotation_enabled": resolution.l2_annotation_enabled,
            "annotation_schema": resolution.schema,
        }

    def _get_video_keys(self) -> list[str]:
        features = self.info.get("features", {}) if self.info else {}
        return sorted([key for key, meta in features.items() if meta.get("dtype") == "video"])

    def _load_existing_annotations(self) -> None:
        self.annotations = {}
        if self.annotations_path and self.annotations_path.exists():
            data = json.loads(self.annotations_path.read_text())
            for ep_str, payload in data.get("episodes", {}).items():
                ep_idx = int(ep_str)
                ann = self._episode_payload_from_storage(payload)
                self._refresh_skill_derivation(ann, ep_idx)
                self.annotations[ep_idx] = ann
            return

        # Fall back to skills.json if present
        skills_path = self.dataset_root / "meta" / "skills.json"
        if skills_path.exists():
            data = json.loads(skills_path.read_text())
            for ep_str, payload in data.get("episodes", {}).items():
                ep_idx = int(ep_str)
                skills = payload.get("skills", [])
                subtasks = [
                    {"start": s["start"], "end": s["end"], "label": s["name"]}
                    for s in skills
                    if "start" in s and "end" in s
                ]
                self.annotations[ep_idx] = EpisodeAnnotations(subtasks=subtasks, high_levels=[])

    def _save_annotations(self) -> None:
        if not self.annotations_path:
            return
        file_version = 3 if skill_derivation_enabled(self.annotation_schema) else (
            2 if self.schema_ref or any(ann.fields for ann in self.annotations.values()) else 1
        )
        payload: dict[str, Any] = {
            "version": file_version,
            "episodes": {},
        }
        if self.schema_ref:
            payload["schema_ref"] = self.schema_ref
        for ep_idx, ann in self.annotations.items():
            ep_payload: dict[str, Any] = {
                "subtasks": ann.subtasks,
                "high_levels": ann.high_levels,
            }
            if ep_idx in self.job_episode_indices or self.active_job is None:
                try:
                    resolution = self._episode_schema_resolution(ep_idx)
                    ep_payload["schema_ref"] = resolution.schema_ref
                    if resolution.task_family:
                        ep_payload["task_family"] = resolution.task_family
                except HTTPException:
                    pass
            if self.active_job is not None:
                ep_payload["job_id"] = self.active_job.job_id
            if ann.skill_segments:
                ep_payload["skill_segments"] = ann.skill_segments
            if ann.skill_cycles:
                ep_payload["skill_cycles"] = ann.skill_cycles
            if ann.skill_derivation_warnings:
                ep_payload["skill_derivation_warnings"] = ann.skill_derivation_warnings
            if ann.outcome:
                ep_payload["outcome"] = ann.outcome
            if ann.fields:
                ep_payload["fields"] = ann.fields
            payload["episodes"][str(ep_idx)] = ep_payload
        self.annotations_path.parent.mkdir(parents=True, exist_ok=True)
        self.annotations_path.write_text(json.dumps(payload, indent=2))

    def _build_summary(self) -> dict[str, Any]:
        assert self.info and self.episodes_df is not None
        fps = float(self.info.get("fps", 30))
        video_key = self.video_key or self._get_video_keys()[0] if self._get_video_keys() else None
        
        # Calculate video offsets for each episode (for concatenated videos)
        episode_video_offsets = self._calculate_video_offsets(video_key, fps) if video_key else {}
        
        episodes = []
        for _, row in self.episodes_df.iterrows():
            ep_idx = int(row["episode_index"])
            if self.active_job is not None and ep_idx not in self.job_episode_indices:
                continue
            length = int(row.get("length", row.get("dataset_to_index", 0) - row.get("dataset_from_index", 0)))
            duration = length / fps if fps else 0.0
            task_text, task_index = self._resolve_episode_task_text(row)

            # Get video timing info for this episode
            video_info = episode_video_offsets.get(ep_idx, {"video_start_time": 0.0, "video_end_time": duration})

            resolution = self._episode_schema_resolution(ep_idx)
            episode_entry: dict[str, Any] = {
                "episode_index": ep_idx,
                "length": length,
                "duration": duration,
                "video_start_time": video_info["video_start_time"],
                "video_end_time": video_info["video_end_time"],
                "task_family": resolution.task_family,
                "schema_ref": resolution.schema_ref,
                "l2_annotation_enabled": resolution.l2_annotation_enabled,
            }
            if task_text:
                episode_entry["task_text"] = task_text
            if task_index is not None:
                episode_entry["task_index"] = task_index
            ann = self.annotations.get(ep_idx)
            episode_entry["annotation_status"] = (
                self._episode_status(ann, ep_idx) if ann is not None else "none"
            )
            episodes.append(episode_entry)

        episode_indices = [int(ep["episode_index"]) for ep in episodes]
        progress = build_annotation_progress(
            self.annotations,
            episode_indices,
            self.annotation_schema,
        )
        return {
            "source": self.source,
            "repo_id": self.repo_id,
            "revision": self.revision,
            "root": str(self.dataset_root),
            "collection_id": self.collection_id,
            "package_id": self.package_id,
            "fps": fps,
            "video_keys": self._get_video_keys(),
            "selected_video_key": self.video_key,
            "annotation_schema": self.annotation_schema,
            "schema_ref": self.schema_ref,
            "active_job": self._active_job_payload(),
            "annotation_progress": progress,
            "episodes": episodes,
        }

    def _calculate_video_offsets(self, video_key: str, fps: float) -> dict[int, dict[str, float]]:
        """Get start/end times for each episode within its video file.
        
        In LeRobot datasets, videos are concatenated so multiple episodes share
        the same video file. The timestamps are stored directly in the episode metadata
        as 'videos/{video_key}/from_timestamp' and 'videos/{video_key}/to_timestamp'.
        """
        if self.episodes_df is None:
            return {}
        
        from_ts_col = f"videos/{video_key}/from_timestamp"
        to_ts_col = f"videos/{video_key}/to_timestamp"
        
        # Check if timestamp columns exist in the dataframe
        has_timestamp_cols = from_ts_col in self.episodes_df.columns and to_ts_col in self.episodes_df.columns
        
        result = {}
        for _, row in self.episodes_df.iterrows():
            ep_idx = int(row["episode_index"])
            length = int(row.get("length", row.get("dataset_to_index", 0) - row.get("dataset_from_index", 0)))
            duration = length / fps if fps else 0.0
            
            # Use the actual timestamps from the episode metadata if available
            if has_timestamp_cols:
                from_ts = row.get(from_ts_col)
                to_ts = row.get(to_ts_col)
                # Handle NaN/None values
                if pd.notna(from_ts) and pd.notna(to_ts):
                    result[ep_idx] = {
                        "video_start_time": float(from_ts),
                        "video_end_time": float(to_ts),
                    }
                else:
                    # Fallback if timestamps are null
                    result[ep_idx] = {"video_start_time": 0.0, "video_end_time": duration}
            else:
                # Fallback: assume each episode starts at 0 (individual video file per episode)
                result[ep_idx] = {"video_start_time": 0.0, "video_end_time": duration}
        
        return result

    def get_episode_video_path(self, episode_index: int, video_key: str | None = None) -> Path:
        self._assert_episode_in_job(episode_index)
        if self.episodes_df is None or self.info is None:
            raise HTTPException(status_code=400, detail="Dataset not loaded")
        video_key = video_key or self.video_key
        if not video_key:
            raise HTTPException(status_code=400, detail="video_key is required")

        row = self.episodes_df[self.episodes_df["episode_index"] == episode_index]
        if row.empty:
            raise HTTPException(status_code=404, detail=f"Episode {episode_index} not found")
        row = row.iloc[0]

        chunk_col = f"videos/{video_key}/chunk_index"
        file_col = f"videos/{video_key}/file_index"
        if chunk_col not in row or file_col not in row:
            raise HTTPException(status_code=400, detail=f"Video key '{video_key}' not available for this dataset")

        chunk_index = int(row[chunk_col])
        file_index = int(row[file_col])
        rel_path = self.info.get("video_path") or "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
        rel_path = rel_path.format(video_key=video_key, chunk_index=chunk_index, file_index=file_index)
        full_path = (self.dataset_root / rel_path).resolve()

        if full_path.exists():
            return full_path

        if self.source == "hf" and self.repo_id:
            hf_hub_download(
                repo_id=self.repo_id,
                repo_type="dataset",
                filename=rel_path,
                revision=self.revision,
                local_dir=self.dataset_root,
            )
            if full_path.exists():
                return full_path

        raise HTTPException(status_code=404, detail=f"Video file not found: {full_path}")

    def get_episode_annotations(self, episode_index: int) -> EpisodeAnnotations:
        self._assert_episode_in_job(episode_index)
        if episode_index not in self.annotations:
            self.annotations[episode_index] = EpisodeAnnotations()
        ann = self.annotations[episode_index]
        self._refresh_skill_derivation(ann, episode_index)
        return ann

    def _episode_frame_bounds(self, episode_index: int) -> tuple[float, int | None]:
        if self.info is None or self.episodes_df is None:
            return 30.0, None
        fps = float(self.info.get("fps", 30))
        row = self.episodes_df[self.episodes_df["episode_index"] == episode_index]
        if row.empty or "length" not in row.columns:
            return fps, None
        length = int(row.iloc[0]["length"])
        return fps, max(0, length - 1)

    def set_episode_annotations(
        self, payload: EpisodeAnnotationsPayload
    ) -> tuple[EpisodeAnnotations, dict[str, Any]]:
        self._assert_episode_in_job(payload.episode_index)
        if payload.outcome is not None and payload.outcome not in VALID_OUTCOMES:
            raise HTTPException(
                status_code=400,
                detail=f"outcome must be one of {sorted(VALID_OUTCOMES)} or null",
            )
        resolution = self._episode_schema_resolution(payload.episode_index)
        if payload.subtasks and not resolution.l2_annotation_enabled:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Episode {payload.episode_index} is unmapped or outside job family; "
                    "L2 annotation disabled (J-02)"
                ),
            )
        subtasks_dicts = [seg.dict() for seg in payload.subtasks]
        episode_schema = resolution.schema
        fps, max_frame = self._episode_frame_bounds(payload.episode_index)
        validate_subtasks(
            episode_schema,
            subtasks_dicts,
            fps=fps,
            max_frame=max_frame,
        )
        old = self.annotations.get(payload.episode_index)
        fields = filter_episode_fields(
            episode_schema,
            self._maybe_reset_skill_review(
                old,
                subtasks_dicts,
                dict(payload.fields or {}),
                payload.episode_index,
            ),
        )
        validate_episode_fields(episode_schema, fields)
        ann = EpisodeAnnotations(
            subtasks=subtasks_dicts,
            high_levels=[seg.dict() for seg in payload.high_levels],
            skill_cycles=list(payload.skill_cycles or []),
            outcome=payload.outcome,
            fields=fields,
        )
        self._refresh_skill_derivation(ann, payload.episode_index)
        validate_skill_cycles(episode_schema, ann.skill_cycles)
        self.annotations[payload.episode_index] = ann
        self._save_annotations()
        response = self._annotation_response(payload.episode_index, ann)
        response["soft_warnings"] = soft_validate_episode(
            subtasks=ann.subtasks,
            outcome=ann.outcome,
            fields=ann.fields,
            skill_cycles=ann.skill_cycles,
            schema=episode_schema,
        )
        return ann, response

    def export_dataset(self, output_dir: str | None = None, copy_videos: bool = False) -> dict[str, Any]:
        if self.dataset_root is None or self.info is None:
            raise HTTPException(status_code=400, detail="Dataset not loaded")

        if output_dir:
            out_root = Path(output_dir).expanduser().resolve()
        else:
            EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
            name = (self.repo_id or "local_dataset").replace("/", "__")
            out_root = EXPORT_ROOT / f"{name}_annotated"

        out_root.mkdir(parents=True, exist_ok=True)

        src_meta = self.dataset_root / "meta"
        dst_meta = out_root / "meta"
        stage_training_meta(src_meta, dst_meta)

        subtasks_df, subtask_map = build_subtasks_lookup_dataframe(self.annotations)
        _, skill_map = build_skill_segments_dataframe(self.annotations, self.annotation_schema)
        cycles_df = build_cycles_dataframe(self.annotations)
        episode_indices = sorted(self.job_episode_indices) if self.active_job else [
            int(x) for x in self.episodes_df["episode_index"].tolist()
        ]
        episodes_meta_df = build_episodes_meta_dataframe(
            self.annotations,
            episode_indices,
            self.episodes_df,
            self.annotation_schema,
            self.schema_ref,
        )
        tasks_df, task_map = build_high_level_dataframe(self.annotations)

        if not subtasks_df.empty:
            subtasks_df.to_parquet(dst_meta / "subtasks.parquet", engine="pyarrow", compression="snappy")
        if not cycles_df.empty:
            cycles_df.to_parquet(dst_meta / "cycles.parquet", engine="pyarrow", compression="snappy")
        if not episodes_meta_df.empty:
            episodes_meta_df.to_parquet(dst_meta / "episodes_meta.parquet", engine="pyarrow", compression="snappy")
        if not tasks_df.empty:
            tasks_df.to_parquet(dst_meta / "tasks_high_level.parquet", engine="pyarrow", compression="snappy")

        row_counts = {
            "episodes": len(episodes_meta_df),
            "cycles": len(cycles_df),
            "subtask_labels": len(subtasks_df),
            "skills_in_memory": len(skill_map),
        }
        manifest = build_export_manifest(
            schema_ref_value=self.schema_ref,
            schema=self.annotation_schema,
            row_counts=row_counts,
            output_dir=str(out_root),
        )
        (dst_meta / "export_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        info_path = dst_meta / "info.json"
        info = json.loads(info_path.read_text())
        info.setdefault("features", {})
        info["features"].setdefault(
            "subtask_index",
            {"dtype": "int64", "shape": [1], "names": None},
        )
        info["features"].setdefault(
            "task_index_high_level",
            {"dtype": "int64", "shape": [1], "names": None},
        )
        info["features"].setdefault(
            "skill_index",
            {"dtype": "int64", "shape": [1], "names": None},
        )
        info_path.write_text(json.dumps(info, indent=2))

        has_success_feature = "next.success" in info.get("features", {})

        # Update data files
        data_dir = self.dataset_root / "data"
        data_files = sorted(data_dir.rglob("*.parquet"))
        if not data_files:
            raise HTTPException(status_code=404, detail="No data parquet files found")

        for src_path in data_files:
            rel_path = src_path.relative_to(self.dataset_root)
            dst_path = out_root / rel_path
            dst_path.parent.mkdir(parents=True, exist_ok=True)

            df = pd.read_parquet(src_path)
            df["subtask_index"] = -1
            df["task_index_high_level"] = -1
            df["skill_index"] = -1

            for ep_idx in df["episode_index"].unique():
                ann = self.annotations.get(int(ep_idx))
                if not ann:
                    continue

                ep_mask = df["episode_index"] == ep_idx
                if ann.subtasks and subtask_map:
                    df.loc[ep_mask, "subtask_index"] = assign_indices_by_segments(
                        df.loc[ep_mask, "timestamp"],
                        ann.subtasks,
                        subtask_map,
                        label_key="label",
                    )

                if ann.skill_segments and skill_map:
                    df.loc[ep_mask, "skill_index"] = assign_indices_by_segments(
                        df.loc[ep_mask, "timestamp"],
                        ann.skill_segments,
                        skill_map,
                        label_key="skill",
                    )
                    df.loc[ep_mask, "task_index_high_level"] = df.loc[ep_mask, "skill_index"]
                elif ann.high_levels and task_map:
                    df.loc[ep_mask, "task_index_high_level"] = assign_indices_by_segments(
                        df.loc[ep_mask, "timestamp"],
                        ann.high_levels,
                        task_map,
                        label_key="task_key",
                    )

                if has_success_feature and ann.outcome in VALID_OUTCOMES:
                    df.loc[ep_mask, "next.success"] = ann.outcome == "success"

            df.to_parquet(dst_path, engine="pyarrow", compression="snappy", index=False)

        # Copy or link videos
        src_videos = self.dataset_root / "videos"
        dst_videos = out_root / "videos"
        if src_videos.exists():
            if dst_videos.is_symlink():
                dst_videos.unlink()
            elif dst_videos.exists():
                shutil.rmtree(dst_videos)
            if copy_videos:
                shutil.copytree(src_videos, dst_videos)
            else:
                try:
                    os.symlink(src_videos, dst_videos, target_is_directory=True)
                except OSError:
                    shutil.copytree(src_videos, dst_videos)

        return {
            "output_dir": str(out_root),
            "subtasks": len(subtasks_df),
            "skills": len(skill_map),
            "cycles": len(cycles_df),
            "episodes_meta": len(episodes_meta_df),
            "tasks_high_level": len(tasks_df),
            "export_contract_version": manifest["export_contract_version"],
        }


def build_subtasks_dataframe(annotations: dict[int, EpisodeAnnotations]) -> tuple[pd.DataFrame, dict[str, int]]:
    """Backward-compatible wrapper."""
    return build_subtasks_lookup_dataframe(annotations)


def build_high_level_dataframe(annotations: dict[int, EpisodeAnnotations]) -> tuple[pd.DataFrame, dict[str, int]]:
    tasks = []
    task_map: dict[str, int] = {}
    for ann in annotations.values():
        for seg in ann.high_levels:
            key = make_task_key(seg)
            if key not in task_map:
                task_map[key] = len(task_map)
                tasks.append(seg)

    rows = []
    for seg in tasks:
        key = make_task_key(seg)
        rows.append(
            {
                "task": f"{seg.get('user_prompt', '')} | {seg.get('robot_utterance', '')}",
                "task_index": task_map[key],
                "user_prompt": seg.get("user_prompt", ""),
                "robot_utterance": seg.get("robot_utterance", ""),
                "skill": seg.get("skill") or "",
                "scenario_type": seg.get("scenario_type") or "",
                "response_type": seg.get("response_type") or "",
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.set_index("task")

    return df, task_map


def make_task_key(seg: dict[str, Any]) -> str:
    return "||".join(
        [
            seg.get("user_prompt", ""),
            seg.get("robot_utterance", ""),
            seg.get("skill", ""),
            seg.get("scenario_type", ""),
            seg.get("response_type", ""),
        ]
    )


def assign_indices_by_segments(timestamps: pd.Series, segments: list[dict[str, Any]], mapping: dict[str, int], label_key: str) -> list[int]:
    values = [-1] * len(timestamps)
    if not segments:
        return values

    segments_sorted = sorted(segments, key=lambda s: float(s.get("start", 0)))
    for i, ts in enumerate(timestamps):
        ts_val = float(ts)
        for seg_idx, seg in enumerate(segments_sorted):
            start = float(seg.get("start", 0))
            end = float(seg.get("end", 0))
            is_last = seg_idx == len(segments_sorted) - 1
            if (start <= ts_val < end) or (is_last and ts_val <= end):
                label = seg.get(label_key, "")
                if label_key == "task_key":
                    label = make_task_key(seg)
                values[i] = mapping.get(label, -1)
                break
    return values


def parse_range(range_header: str, file_size: int) -> tuple[int, int] | None:
    match = re.match(r"bytes=(\d+)-(\d*)", range_header)
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else file_size - 1
    if start >= file_size:
        return None
    end = min(end, file_size - 1)
    return start, end


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = DataManager()


@app.get("/")
def root() -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>LeRobot Annotate</h1><p>Missing static index.html</p>")
    return HTMLResponse(index_path.read_text())


@app.get("/api/datasets/collections")
def api_list_collections() -> JSONResponse:
    collections = list_collections()
    summaries = [
        {
            "id": c.get("id"),
            "title": c.get("title"),
            "description": c.get("description"),
            "robot_type": c.get("robot_type"),
            "task_family": c.get("task_family"),
            "package_count": c.get("package_count", len(c.get("packages") or [])),
            "kind": c.get("kind", "collection"),
        }
        for c in collections
    ]
    return JSONResponse({"collections": summaries, "datasets_root": str(datasets_root())})


@app.get("/api/datasets/collections/{collection_id}")
def api_get_collection(collection_id: str) -> JSONResponse:
    try:
        collection = get_collection(collection_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Collection not found: {collection_id}") from exc
    return JSONResponse(collection)


@app.get("/api/datasets/collections/{collection_id}/jobs")
def api_list_jobs(
    collection_id: str,
    package_id: str | None = None,
    task_family: str | None = None,
    status: str | None = None,
) -> JSONResponse:
    from annotation_jobs_service import count_annotated_episodes

    collection_dir = datasets_root() / collection_id
    if not collection_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Collection not found: {collection_id}")
    jobs = filter_jobs(
        load_collection_jobs(collection_dir),
        package_id=package_id,
        task_family=task_family,
        status=status,
    )
    family_map = load_task_family_map(collection_dir)
    summaries: list[dict[str, Any]] = []
    for job in jobs:
        pkg_root = collection_dir / job.package_id
        if not pkg_root.is_dir():
            continue
        ep_files = sorted((pkg_root / "meta" / "episodes").rglob("*.parquet"))
        if not ep_files:
            continue
        episodes_df = pd.concat([pd.read_parquet(f) for f in ep_files], ignore_index=True)
        annotations: dict[int, Any] = {}
        ann_path = pkg_root / "meta" / "lerobot_annotations.json"
        if ann_path.is_file():
            raw = json.loads(ann_path.read_text(encoding="utf-8"))
            annotations = {int(k): v for k, v in (raw.get("episodes") or {}).items()}
        dm = DataManager()
        dm.tasks_map = dm._load_tasks_map(pkg_root)
        indices = compute_job_episode_indices(
            job,
            episodes_df,
            family_map=family_map,
            resolve_task_text=dm._resolve_episode_task_text,
        )
        try:
            schema = load_schema_by_id(job.schema_id, package_root=pkg_root)
            annotated = count_annotated_episodes(indices, annotations, schema)
        except FileNotFoundError:
            annotated = 0
        summaries.append(job_to_summary(job, episode_count=len(indices), annotated_count=annotated))
    return JSONResponse({"collection_id": collection_id, "jobs": summaries})


@app.get("/api/datasets/collections/{collection_id}/jobs/{job_id}")
def api_get_job(collection_id: str, job_id: str) -> JSONResponse:
    collection_dir = datasets_root() / collection_id
    job = get_job_by_id(collection_dir, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    package_root = collection_dir / job.package_id
    if not package_root.is_dir():
        raise HTTPException(status_code=404, detail=f"Package not found: {job.package_id}")
    family_map = load_task_family_map(collection_dir)
    episodes_df = pd.concat(
        [pd.read_parquet(f) for f in sorted((package_root / "meta" / "episodes").rglob("*.parquet"))],
        ignore_index=True,
    )
    annotations: dict[int, Any] = {}
    ann_path = package_root / "meta" / "lerobot_annotations.json"
    if ann_path.is_file():
        raw = json.loads(ann_path.read_text(encoding="utf-8"))
        annotations = {int(k): v for k, v in (raw.get("episodes") or {}).items()}
    dm = DataManager()
    dm.tasks_map = dm._load_tasks_map(package_root)
    detail = build_job_detail(
        job,
        collection_dir=collection_dir,
        package_root=package_root,
        episodes_df=episodes_df,
        family_map=family_map,
        annotations=annotations,
        resolve_task_text=dm._resolve_episode_task_text,
    )
    return JSONResponse(detail)


@app.get("/api/episodes/{episode_index}/resolve")
def api_resolve_episode(episode_index: int, job_id: str | None = None) -> JSONResponse:
    if manager.dataset_root is None:
        raise HTTPException(status_code=400, detail="Dataset not loaded")
    manager._assert_episode_in_job(episode_index)
    resolution = manager._episode_schema_resolution(episode_index)
    row = manager._episode_row(episode_index)
    task_text, task_index = manager._resolve_episode_task_text(row)
    return JSONResponse(
        {
            "episode_index": episode_index,
            "task_text": task_text,
            "task_index": task_index,
            "task_family": resolution.task_family,
            "schema_ref": resolution.schema_ref,
            "l2_annotation_enabled": resolution.l2_annotation_enabled,
            "resolution_source": resolution.resolution_source,
            "in_job": episode_index in manager.job_episode_indices,
            "active_job": manager._active_job_payload(),
        }
    )


@app.post("/api/dataset/load")
def load_dataset(req: DatasetLoadRequest) -> JSONResponse:
    summary = manager.load_dataset(req)
    return JSONResponse(summary)


@app.get("/api/dataset/info")
def dataset_info() -> JSONResponse:
    if manager.info is None:
        raise HTTPException(status_code=400, detail="Dataset not loaded")
    return JSONResponse(manager._build_summary())


@app.get("/api/episodes/{episode_index}/annotations")
def get_annotations(episode_index: int) -> JSONResponse:
    ann = manager.get_episode_annotations(episode_index)
    return JSONResponse(manager._annotation_response(episode_index, ann))


@app.post("/api/episodes/{episode_index}/annotations")
def set_annotations(episode_index: int, payload: EpisodeAnnotationsPayload) -> JSONResponse:
    if episode_index != payload.episode_index:
        raise HTTPException(status_code=400, detail="Episode index mismatch")
    ann, response = manager.set_episode_annotations(payload)
    return JSONResponse({"ok": True, **response})


@app.post("/api/export")
def export_dataset(payload: dict[str, Any]) -> JSONResponse:
    output_dir = payload.get("output_dir")
    copy_videos = bool(payload.get("copy_videos", False))
    result = manager.export_dataset(output_dir=output_dir, copy_videos=copy_videos)
    return JSONResponse(result)


class PushToHubRequest(BaseModel):
    hf_token: str
    push_in_place: bool = True
    new_repo_id: str | None = None
    private: bool = False
    commit_message: str = "Add annotations from LeRobot Annotate"


class EgoSubtaskSegment(BaseModel):
    start_frame: int
    end_frame: int
    subtask_index: int
    subtask_name: str


class EgoSaveRequest(BaseModel):
    dataset_path: str
    episode_index: int = 0
    subtasks: list[EgoSubtaskSegment] = Field(default_factory=list)


@app.get("/api/ego/load")
def ego_load(datasetPath: str, episodeIndex: int = 0) -> JSONResponse:
    root = Path(datasetPath).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Dataset path not found: {root}")
    try:
        info = load_info(root)
        row = episode_row(root, episodeIndex)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    total_frames = episode_total_frames(root, episodeIndex, info)
    boundaries = read_annotation_boundaries(root, episodeIndex)
    return JSONResponse(
        {
            "episode_meta": episode_meta_public(row),
            "total_frames": total_frames,
            "fps": float(info.get("fps") or 30),
            "features": scalar_features(info),
            "subtasks": boundaries,
            "segments": segments_from_boundaries(boundaries, total_frames),
        }
    )


@app.get("/api/ego/hand_poses")
def ego_hand_poses(datasetPath: str, episodeIndex: int = 0) -> JSONResponse:
    root = Path(datasetPath).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Dataset path not found: {root}")
    try:
        load_info(root)
        poses = read_hand_poses_for_episode(root, episodeIndex)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse({"episode_index": episodeIndex, "poses": poses})


@app.post("/api/ego/save")
def ego_save(payload: EgoSaveRequest) -> JSONResponse:
    root = Path(payload.dataset_path).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Dataset path not found: {root}")
    episode_index = int(payload.episode_index)
    try:
        row = episode_row(root, episode_index)
        status = str(row.get("annotation_status") or "raw")
        if status == "failed":
            raise HTTPException(status_code=400, detail="cannot annotate episode with annotation_status=failed")
        if status not in ("raw", "hand_done", "subtask_done"):
            raise HTTPException(status_code=400, detail=f"unsupported annotation_status: {status}")

        segments = [seg.dict() for seg in payload.subtasks]
        updated_rows = write_annotations_for_episode(root, episode_index, segments)
        update_episode_after_subtask_save(root, episode_index)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse({"success": True, "updated_rows": updated_rows})


@app.post("/api/push_to_hub")
def push_to_hub(payload: PushToHubRequest) -> JSONResponse:
    """Push the annotated dataset to Hugging Face Hub.
    
    Can either update the original repo in place, or push to a new repo.
    """
    print("[Push to Hub] Received push request")
    print(f"[Push to Hub] push_in_place={payload.push_in_place}, new_repo_id={payload.new_repo_id}")
    
    if manager.dataset_root is None or manager.info is None:
        print("[Push to Hub] Error: Dataset not loaded")
        raise HTTPException(status_code=400, detail="Dataset not loaded")
    
    if manager.source != "hf":
        print(f"[Push to Hub] Error: Source is '{manager.source}', not 'hf'")
        raise HTTPException(status_code=400, detail="Can only push to Hub for datasets loaded from Hub")
    
    # Download data files if they don't exist (they weren't downloaded during initial load)
    data_dir = manager.dataset_root / "data"
    data_files_exist = data_dir.exists() and list(data_dir.rglob("*.parquet"))
    if not data_files_exist:
        print("[Push to Hub] Data files not found locally, downloading from Hub...")
        if not manager.repo_id:
            raise HTTPException(status_code=400, detail="No repo ID available to download data files")
        try:
            snapshot_download(
                manager.repo_id,
                repo_type="dataset",
                revision=manager.revision,
                local_dir=manager.dataset_root,
                allow_patterns=["data/**/*.parquet"],
            )
            print("[Push to Hub] Data files downloaded successfully")
        except Exception as e:
            print(f"[Push to Hub] Error downloading data files: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to download data files: {str(e)}")
    
    # Download videos if they don't exist (we always copy videos when pushing to hub)
    videos_dir = manager.dataset_root / "videos"
    if not videos_dir.exists():
        print("[Push to Hub] Videos not found locally, downloading from Hub...")
        if not manager.repo_id:
            raise HTTPException(status_code=400, detail="No repo ID available to download videos")
        try:
            snapshot_download(
                manager.repo_id,
                repo_type="dataset",
                revision=manager.revision,
                local_dir=manager.dataset_root,
                allow_patterns=["videos/**/*.mp4"],
            )
            print("[Push to Hub] Videos downloaded successfully")
        except Exception as e:
            print(f"[Push to Hub] Warning: Could not download videos: {e}")
            # Videos are optional, so we continue even if download fails
    
    # First, export the dataset locally
    print("[Push to Hub] Exporting dataset locally...")
    export_result = manager.export_dataset(copy_videos=True)
    export_dir = Path(export_result["output_dir"])
    print(f"[Push to Hub] Exported to: {export_dir}")
    
    # Determine target repo
    if payload.push_in_place:
        if not manager.repo_id:
            print("[Push to Hub] Error: No original repo ID found")
            raise HTTPException(status_code=400, detail="No original repo ID found")
        target_repo = manager.repo_id
    else:
        if not payload.new_repo_id:
            print("[Push to Hub] Error: New repo ID is required")
            raise HTTPException(status_code=400, detail="New repo ID is required when not pushing in place")
        target_repo = payload.new_repo_id
    
    print(f"[Push to Hub] Target repo: {target_repo}")
    
    try:
        print("[Push to Hub] Initializing HfApi...")
        api = HfApi(token=payload.hf_token)
        
        # Create repo if pushing to new location
        if not payload.push_in_place:
            print(f"[Push to Hub] Creating new repo: {target_repo}")
            try:
                api.create_repo(
                    repo_id=target_repo,
                    repo_type="dataset",
                    private=payload.private,
                    exist_ok=True,
                )
                print("[Push to Hub] Repo created successfully")
            except Exception as e:
                print(f"[Push to Hub] Error creating repo: {e}")
                raise HTTPException(status_code=400, detail=f"Failed to create repo: {str(e)}")
        
        # Upload the entire exported directory
        print(f"[Push to Hub] Uploading folder to {target_repo}...")
        api.upload_folder(
            folder_path=str(export_dir),
            repo_id=target_repo,
            repo_type="dataset",
            commit_message=payload.commit_message,
        )
        
        print(f"[Push to Hub] Successfully pushed to {target_repo}")
        return JSONResponse({
            "ok": True,
            "repo_id": target_repo,
            "url": f"https://huggingface.co/datasets/{target_repo}",
            "message": f"Successfully pushed to {target_repo}",
        })
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Push to Hub] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to push to Hub: {str(e)}")


@app.get("/api/debug/columns")
def debug_columns() -> JSONResponse:
    """Debug endpoint to see what columns are available in the episodes dataframe."""
    if manager.episodes_df is None:
        raise HTTPException(status_code=400, detail="Dataset not loaded")
    return JSONResponse({
        "columns": list(manager.episodes_df.columns),
        "sample_row": manager.episodes_df.iloc[0].to_dict() if len(manager.episodes_df) > 0 else {},
    })


@app.get("/api/episodes/{episode_index}/video_timing")
def get_episode_video_timing(episode_index: int, video_key: str | None = None) -> JSONResponse:
    """Get video timing information for a specific episode.
    
    Returns the start and end timestamps within the video file for this episode.
    This is needed because LeRobot datasets concatenate videos for faster reading.
    """
    if manager.episodes_df is None or manager.info is None:
        raise HTTPException(status_code=400, detail="Dataset not loaded")
    
    video_key = video_key or manager.video_key
    fps = float(manager.info.get("fps", 30))
    
    # Get the episode row
    row = manager.episodes_df[manager.episodes_df["episode_index"] == episode_index]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"Episode {episode_index} not found")
    row = row.iloc[0]
    
    length = int(row.get("length", row.get("dataset_to_index", 0) - row.get("dataset_from_index", 0)))
    duration = length / fps if fps else 0.0
    
    # Calculate video offset
    video_offsets = manager._calculate_video_offsets(video_key, fps) if video_key else {}
    video_info = video_offsets.get(episode_index, {"video_start_time": 0.0, "video_end_time": duration})
    
    return JSONResponse({
        "episode_index": episode_index,
        "fps": fps,
        "length": length,
        "duration": duration,
        "video_start_time": video_info["video_start_time"],
        "video_end_time": video_info["video_end_time"],
    })


@app.get("/api/video/{episode_index}")
def stream_video(episode_index: int, request: Request, video_key: str | None = None) -> Response:
    """Stream video for a specific episode.
    
    For concatenated videos (where multiple episodes share one file), this endpoint
    will trim the video to only include the relevant episode portion using FFmpeg.
    """
    video_key = video_key or manager.video_key
    original_path = manager.get_episode_video_path(episode_index, video_key=video_key)
    
    # Get the video timing for this episode
    fps = float(manager.info.get("fps", 30)) if manager.info else 30.0
    video_offsets = manager._calculate_video_offsets(video_key, fps) if video_key else {}
    video_info = video_offsets.get(episode_index, {"video_start_time": 0.0, "video_end_time": 0.0})
    
    start_time = video_info["video_start_time"]
    end_time = video_info["video_end_time"]
    
    # Determine if we need to trim the video
    # If start_time > 0, it means this episode is part of a concatenated video
    needs_trimming = start_time > 0.1 or (end_time > 0 and end_time < get_video_duration(original_path) - 0.5)
    
    if needs_trimming and end_time > start_time:
        # Check if we have a cached trimmed version
        cache_path = get_trimmed_video_cache_path(original_path, episode_index, start_time, end_time)
        
        if not cache_path.exists():
            # Trim the video and cache it
            print(f"Trimming video for episode {episode_index}: {start_time:.2f}s - {end_time:.2f}s")
            success = trim_video_with_ffmpeg(original_path, cache_path, start_time, end_time)
            if not success:
                print(f"Failed to trim video, falling back to full video")
                # Fall back to full video if trimming fails
                path = original_path
            else:
                path = cache_path
        else:
            path = cache_path
    else:
        path = original_path
    
    file_size = path.stat().st_size
    range_header = request.headers.get("range")

    if range_header:
        byte_range = parse_range(range_header, file_size)
        if not byte_range:
            return Response(status_code=416)
        start, end = byte_range
        length = end - start + 1

        def iterfile() -> Any:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        }
        return StreamingResponse(iterfile(), status_code=206, media_type="video/mp4", headers=headers)

    return FileResponse(path, media_type="video/mp4")


def get_video_duration(video_path: Path) -> float:
    """Get the duration of a video file using FFprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception as e:
        print(f"Error getting video duration: {e}")
    return 0.0


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
