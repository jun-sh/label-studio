from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CameraPoseSample(BaseModel):
    """World-from-camera pose at a single time."""

    t_ns: int = Field(..., description="Timestamp in nanoseconds (aligned domain).")
    quat_wxyz: tuple[float, float, float, float]
    trans_xyz: tuple[float, float, float]


class HeadPoseSample(BaseModel):
    t_ns: int
    quat_wxyz: tuple[float, float, float, float]
    trans_xyz: tuple[float, float, float]
    source: Literal["rigid_camera", "external_tracker", "estimated"]


class LanguageSegment(BaseModel):
    t_start_ns: int
    t_end_ns: int
    text: str
    speaker: str | None = None


class EpisodeManifest(BaseModel):
    """Episode-level metadata aligned with dense annotation + task coverage workflows."""

    model_config = ConfigDict(extra="ignore")

    episode_id: str
    schema_version: str = "1.0"
    task_name: str | None = None
    task_tier: str | None = Field(
        default=None,
        description="flagship | open_ended | mixed — standardized vs open-ended coverage.",
    )
    operator_id: str | None = None
    scene_id: str | None = None
    object_tags: list[str] = Field(default_factory=list)
    dense_annotation_types: list[str] = Field(
        default_factory=list,
        description="Declared dense channels, e.g. camera_pose, head_3d, language_dense.",
    )
    duration_ns: int | None = None
    streams: dict[str, str] = Field(default_factory=dict)
    notes: str | None = None


class PolicyReadyBundle(BaseModel):
    """Minimal contract for downstream policy training."""

    manifest: EpisodeManifest
    poses: list[CameraPoseSample]
    head: list[HeadPoseSample]
    language: list[LanguageSegment]
    assets: dict[str, str] = Field(
        default_factory=dict,
        description="Relative paths inside episode root (video, depth, etc.).",
    )
