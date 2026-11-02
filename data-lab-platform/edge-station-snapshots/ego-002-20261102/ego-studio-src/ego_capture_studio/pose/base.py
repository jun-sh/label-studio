from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ego_capture_studio.schema.episode import CameraPoseSample


class PoseProvider(ABC):
    @abstractmethod
    def load(self, episode_root: Path) -> list[CameraPoseSample]:
        raise NotImplementedError


class IdentityPoseProvider(PoseProvider):
    """Single identity pose at t=0 — for dry runs without SLAM."""

    def load(self, episode_root: Path) -> list[CameraPoseSample]:
        _ = episode_root
        return [
            CameraPoseSample(
                t_ns=0,
                quat_wxyz=(1.0, 0.0, 0.0, 0.0),
                trans_xyz=(0.0, 0.0, 0.0),
            )
        ]
