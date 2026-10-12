from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ego_capture_studio.export.bundle_writer import write_policy_bundle
from ego_capture_studio.head.base import HeadTrackingProvider, NoHeadProvider, RigidHeadFromCamera
from ego_capture_studio.ingest.av_bundle import AVBundle
from ego_capture_studio.ingest.sync import ClockModel
from ego_capture_studio.language.transcripts import JsonFileLanguageProvider
from ego_capture_studio.pose.base import IdentityPoseProvider, PoseProvider
from ego_capture_studio.pose.file_jsonl import FileJsonlPoseProvider
from ego_capture_studio.qc.checks import QCConfig, run_qc
from ego_capture_studio.schema.episode import EpisodeManifest, PolicyReadyBundle


@dataclass
class PipelineConfig:
    raw: dict[str, Any]

    @classmethod
    def from_yaml(cls, path: Path) -> PipelineConfig:
        return cls(raw=yaml.safe_load(path.read_text(encoding="utf-8")))


def _pose_provider(cfg: dict[str, Any]) -> PoseProvider:
    name = cfg.get("provider", "file_jsonl")
    if name == "identity":
        return IdentityPoseProvider()
    if name == "file_jsonl":
        return FileJsonlPoseProvider(relative_name=str(cfg.get("poses_jsonl", "poses.jsonl")))
    raise ValueError(f"Unknown pose provider: {name}")


def _head_provider(cfg: dict[str, Any]) -> HeadTrackingProvider:
    name = cfg.get("provider", "rigid_camera")
    if name == "rigid_camera":
        return RigidHeadFromCamera()
    if name == "none":
        return NoHeadProvider()
    raise ValueError(f"Unknown head provider: {name}")


def run_episode_pipeline(
    episode_root: Path,
    pipeline_cfg: PipelineConfig,
    manifest: EpisodeManifest,
) -> Path:
    episode_root = episode_root.resolve()
    av = AVBundle.discover(episode_root)
    clock = ClockModel(
        master_domain=pipeline_cfg.raw.get("clock", {}).get("master_domain", "monotonic_ns"),
        av_timebase=pipeline_cfg.raw.get("clock", {}).get("av_timebase", "best_effort_pts"),
    )
    _ = clock
    _ = av

    pose_cfg = pipeline_cfg.raw.get("pose", {})
    poses = _pose_provider(pose_cfg).load(episode_root)

    head = _head_provider(pipeline_cfg.raw.get("head", {})).from_camera_poses(poses)

    lang_cfg = pipeline_cfg.raw.get("language", {})
    if lang_cfg.get("provider", "json_file") != "json_file":
        raise NotImplementedError("Only json_file language provider is implemented in core.")
    segments = JsonFileLanguageProvider(
        relative_name=str(lang_cfg.get("transcript_json", "language.json"))
    ).load(episode_root)

    streams: dict[str, str] = {}
    if av.video:
        streams["video"] = str(av.video.relative_to(episode_root))
    if av.video_left:
        streams["video_left"] = str(av.video_left.relative_to(episode_root))
    if av.video_right:
        streams["video_right"] = str(av.video_right.relative_to(episode_root))
    if av.audio:
        streams["audio"] = str(av.audio.relative_to(episode_root))

    manifest = manifest.model_copy(update={"streams": streams})

    bundle = PolicyReadyBundle(
        manifest=manifest,
        poses=poses,
        head=head,
        language=segments,
        assets=dict(streams),
    )

    qc_raw = pipeline_cfg.raw.get("qc", {})
    qc_cfg = QCConfig(
        require_poses=bool(qc_raw.get("require_poses", False)),
        require_language=bool(qc_raw.get("require_language", False)),
        max_pose_gap_ns=int(qc_raw.get("max_pose_gap_ns", 200_000_000)),
    )
    run_qc(bundle, qc_cfg)

    export_cfg = pipeline_cfg.raw.get("export", {})
    out_dir = episode_root / str(export_cfg.get("output_subdir", "export"))
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = out_dir / "policy_ready_bundle.json"
    write_policy_bundle(bundle_path, bundle)
    (out_dir / "clock_model.json").write_text(
        __import__("json").dumps({"master_domain": clock.master_domain, "av_timebase": clock.av_timebase}),
        encoding="utf-8",
    )
    return bundle_path
