from __future__ import annotations

from dataclasses import dataclass

from ego_capture_studio.schema.episode import LanguageSegment, PolicyReadyBundle


@dataclass
class QCConfig:
    require_poses: bool = False
    require_language: bool = False
    max_pose_gap_ns: int = 200_000_000


class QCFailed(Exception):
    pass


def run_qc(bundle: PolicyReadyBundle, cfg: QCConfig) -> list[str]:
    warnings: list[str] = []
    if cfg.require_poses and not bundle.poses:
        raise QCFailed("QC: poses required but empty")
    if cfg.require_language and not bundle.language:
        raise QCFailed("QC: language required but empty")

    if len(bundle.poses) >= 2:
        times = sorted(p.t_ns for p in bundle.poses)
        for a, b in zip(times, times[1:]):
            if b - a > cfg.max_pose_gap_ns:
                warnings.append(f"QC: pose gap {b - a} ns exceeds max_pose_gap_ns")

    def _overlap(a: LanguageSegment, b: LanguageSegment) -> bool:
        return not (a.t_end_ns <= b.t_start_ns or b.t_end_ns <= a.t_start_ns)

    lang = bundle.language
    for i, s in enumerate(lang):
        if s.t_end_ns < s.t_start_ns:
            raise QCFailed("QC: language segment end before start")
        for t in lang[i + 1 :]:
            if _overlap(s, t):
                warnings.append("QC: overlapping language segments detected")
                break
    return warnings
