"""FSYNC-quad POC diagnostics: per-cam ingest Hz, quad miss rate, wall commit rate."""

from __future__ import annotations

import os
import time
from collections import Counter
from typing import Any


def fsync_quad_diag_enabled() -> bool:
    return os.environ.get("EGO_FSYNC_QUAD_DIAG", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


class FsyncQuadDiagnostics:
    def __init__(self) -> None:
        self._report_every = max(30, int(os.environ.get("EGO_FSYNC_QUAD_DIAG_EVERY", "90")))
        self._wall_start = time.monotonic()
        self._ingest_start: dict[str, int] = {}
        self._parallel_rings: dict[str, Any] | None = None
        self._quad_commits = 0
        self._quad_misses = 0
        self._quad_miss_by_cam: Counter[str] = Counter()
        self._max_offset_ns_sum = 0
        self._max_offset_ns_n = 0

    def snapshot_ingest(self, parallel_rings: dict[str, Any]) -> None:
        if not fsync_quad_diag_enabled():
            return
        self._parallel_rings = parallel_rings
        for oak, ring in parallel_rings.items():
            self._ingest_start[oak] = int(ring.total_appends)

    def note_quad_commit(self, offsets: dict[str, int]) -> None:
        if not fsync_quad_diag_enabled():
            return
        self._quad_commits += 1
        if offsets:
            self._max_offset_ns_sum += max(abs(int(v)) for v in offsets.values())
            self._max_offset_ns_n += 1
        if self._quad_commits % self._report_every == 0:
            self.report(prefix="fsync_quad_interval", parallel_rings=self._parallel_rings)

    def note_quad_miss(self, blocking_cam: str | None) -> None:
        if not fsync_quad_diag_enabled():
            return
        self._quad_misses += 1
        if blocking_cam:
            self._quad_miss_by_cam[blocking_cam] += 1

    def _ingest_hz(self, parallel_rings: dict[str, Any] | None) -> dict[str, float]:
        if not parallel_rings or not self._ingest_start:
            return {}
        wall_s = max(time.monotonic() - self._wall_start, 1e-6)
        out: dict[str, float] = {}
        for oak, ring in parallel_rings.items():
            start = self._ingest_start.get(oak, 0)
            delta = int(ring.total_appends) - int(start)
            out[oak] = delta / wall_s
        return out

    def report(self, *, prefix: str = "fsync_quad_final", parallel_rings: dict[str, Any] | None) -> None:
        if not fsync_quad_diag_enabled():
            return
        wall_s = max(time.monotonic() - self._wall_start, 1e-6)
        attempts = self._quad_commits + self._quad_misses
        miss_pct = 100.0 * self._quad_misses / attempts if attempts else 0.0
        wall_commit_hz = self._quad_commits / wall_s
        ingest = self._ingest_hz(parallel_rings)
        avg_max_off_us = 0.0
        if self._max_offset_ns_n > 0:
            avg_max_off_us = self._max_offset_ns_sum / self._max_offset_ns_n / 1000.0
        ingest_items = sorted(ingest.items())
        print(
            f"{prefix} commits={self._quad_commits} misses={self._quad_misses} "
            f"quad_miss_pct={miss_pct:.2f} wall_commit_hz={wall_commit_hz:.2f} "
            f"per_cam_ingest_hz={ingest_items} quad_miss_by_cam={self._quad_miss_by_cam.most_common(4)} "
            f"avg_max_cam_offset_us={avg_max_off_us:.1f}",
            flush=True,
        )
