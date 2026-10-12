"""Per-frame capture timing (POC / parallel-drain line only)."""

from __future__ import annotations

import os
import time
from collections import defaultdict


def profile_enabled() -> bool:
    return os.environ.get("EGO_CAPTURE_PROFILE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


class FrameProfiler:
    """Accumulate wall-time buckets; report ms/frame every N frames."""

    _instance: FrameProfiler | None = None

    def __init__(self) -> None:
        self._sums: dict[str, float] = defaultdict(float)
        self._counts: dict[str, int] = defaultdict(int)
        self._frame_count = 0
        self._report_every = max(30, int(os.environ.get("EGO_CAPTURE_PROFILE_EVERY", "90")))
        self._t0 = time.perf_counter()

    @classmethod
    def get(cls) -> FrameProfiler | None:
        if not profile_enabled():
            return None
        if cls._instance is None:
            cls._instance = FrameProfiler()
        return cls._instance

    def add(self, bucket: str, seconds: float, *, frames: int = 1) -> None:
        self._sums[bucket] += float(seconds)
        self._counts[bucket] += int(frames)

    def tick_frames(self, n: int = 1) -> None:
        self._frame_count += int(n)
        if self._frame_count % self._report_every == 0:
            self.report(prefix="capture_profile_interval")

    def report(self, *, prefix: str = "capture_profile_final") -> None:
        if not self._counts:
            return
        elapsed = max(time.perf_counter() - self._t0, 1e-9)
        frames = max(self._frame_count, 1)
        parts: list[str] = []
        bucket_ms_sum = 0.0
        for bucket in sorted(self._sums.keys()):
            ms_per = (self._sums[bucket] / max(self._counts[bucket], 1)) * 1000.0
            parts.append(f"{bucket}={ms_per:.2f}ms")
            bucket_ms_sum += ms_per
        parts.append(f"buckets_sum={bucket_ms_sum:.2f}ms/f")
        parts.append(f"wall={elapsed:.1f}s frames={frames} wall_fps={frames/elapsed:.2f}")
        print(f"{prefix} " + " ".join(parts), flush=True)
