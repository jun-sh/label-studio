from __future__ import annotations

from abc import ABC, abstractmethod

from ego_capture_studio.schema.episode import CameraPoseSample, HeadPoseSample


class HeadTrackingProvider(ABC):
    @abstractmethod
    def from_camera_poses(self, poses: list[CameraPoseSample]) -> list[HeadPoseSample]:
        raise NotImplementedError


class RigidHeadFromCamera(HeadTrackingProvider):
    """Head frame equals camera frame (common for head-mounted egocentric rigs)."""

    def from_camera_poses(self, poses: list[CameraPoseSample]) -> list[HeadPoseSample]:
        return [
            HeadPoseSample(
                t_ns=p.t_ns,
                quat_wxyz=p.quat_wxyz,
                trans_xyz=p.trans_xyz,
                source="rigid_camera",
            )
            for p in poses
        ]


class NoHeadProvider(HeadTrackingProvider):
    def from_camera_poses(self, poses: list[CameraPoseSample]) -> list[HeadPoseSample]:
        _ = poses
        return []
