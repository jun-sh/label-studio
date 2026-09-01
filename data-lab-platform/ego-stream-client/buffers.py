"""In-memory episode buffers for OAK capture."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class EpisodeBuffers:
    rgb_ts_ns: list[int] = field(default_factory=list)
    gyro_ts_ns: list[int] = field(default_factory=list)
    gyro_xyz: list[tuple[float, float, float]] = field(default_factory=list)
    accel_ts_ns: list[int] = field(default_factory=list)
    accel_xyz: list[tuple[float, float, float]] = field(default_factory=list)
    camera_frames: dict[str, list[Any]] = field(default_factory=lambda: defaultdict(list))
    camera_intrinsics: dict[str, Any] = field(default_factory=dict)

    def video_shapes(self) -> dict[str, tuple[int, int]]:
        shapes: dict[str, tuple[int, int]] = {}
        for key, frames in self.camera_frames.items():
            if not frames:
                continue
            frame0 = frames[0]
            if isinstance(frame0, np.ndarray) and frame0.ndim == 3:
                shapes[key] = (int(frame0.shape[0]), int(frame0.shape[1]))
        return shapes
