from __future__ import annotations

import json
from pathlib import Path

from ego_capture_studio.pose.base import PoseProvider
from ego_capture_studio.schema.episode import CameraPoseSample


class FileJsonlPoseProvider(PoseProvider):
    """Load poses produced offline (COLMAP, OpenVINS, device export, etc.)."""

    def __init__(self, relative_name: str = "poses.jsonl") -> None:
        self.relative_name = relative_name

    def load(self, episode_root: Path) -> list[CameraPoseSample]:
        path = episode_root / self.relative_name
        if not path.is_file():
            return []
        out: list[CameraPoseSample] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            out.append(
                CameraPoseSample(
                    t_ns=int(row["t_ns"]),
                    quat_wxyz=tuple(row["quat_wxyz"]),
                    trans_xyz=tuple(row["trans_xyz"]),
                )
            )
        return out
