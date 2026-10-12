"""Per-camera ring straggler diagnostics for parallel-drain POC."""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from typing import Any


def straggler_log_enabled() -> bool:
    return os.environ.get("EGO_STRAGGLER_LOG", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


class StragglerDiagnostics:
    """Track which camera ring is shallowest / empty when commit stalls."""

    def __init__(self) -> None:
        self._report_every = max(30, int(os.environ.get("EGO_STRAGGLER_LOG_EVERY", "90")))
        self._frame_count = 0
        self._min_depth_counts: Counter[str] = Counter()
        self._empty_wait_counts: Counter[str] = Counter()
        self._max_offset_ns_sum: dict[str, int] = defaultdict(int)
        self._max_offset_ns_n = 0

    def note_pre_commit_depths(
        self,
        depths: dict[str, int],
        offsets: dict[str, int],
    ) -> None:
        if not straggler_log_enabled() or not depths:
            return
        shallowest = min(depths, key=depths.get)
        self._min_depth_counts[shallowest] += 1
        if offsets:
            max_off = max(abs(int(v)) for v in offsets.values())
            self._max_offset_ns_sum["max"] += max_off
            self._max_offset_ns_n += 1
        self._frame_count += 1
        if self._frame_count % self._report_every == 0:
            self.report(prefix="straggler_interval")

    def note_wait_empty(self, cam_rings: dict[str, Any], cam_list: list[str]) -> None:
        if not straggler_log_enabled():
            return
        for oak in cam_list:
            if not cam_rings.get(oak):
                self._empty_wait_counts[oak] += 1

    def report(self, *, prefix: str = "straggler_final") -> None:
        if not straggler_log_enabled() or self._frame_count == 0:
            return
        top_min = self._min_depth_counts.most_common(4)
        top_empty = self._empty_wait_counts.most_common(4)
        avg_max_off_us = 0.0
        if self._max_offset_ns_n > 0:
            avg_max_off_us = self._max_offset_ns_sum["max"] / self._max_offset_ns_n / 1000.0
        print(
            f"{prefix} frames={self._frame_count} "
            f"shallowest_at_commit={top_min} "
            f"empty_wait={top_empty} "
            f"avg_max_cam_offset_us={avg_max_off_us:.1f}",
            flush=True,
        )
