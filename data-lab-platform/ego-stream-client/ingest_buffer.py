"""Deep ingest rings + overflow accounting for parallel-drain POC."""

from __future__ import annotations

import os
from collections import Counter, deque

from ego_capture_studio.capture.oak_4p_capture import (
    DEPTH_OAK_SOCKET,
    PRIMARY_OAK_SOCKET,
    STRICT_DEPTH_RING_LEN,
    STRICT_PRIMARY_RING_LEN,
    STRICT_RGB_RING_LEN,
)

EGO_POC_RING_LEN = max(0, int(os.environ.get("EGO_POC_RING_LEN", "512")))


def poc_ring_len_enabled() -> bool:
    return EGO_POC_RING_LEN > 0


def poc_ring_len_for_oak(oak_socket: str) -> int:
    """POC ring depth override; falls back to production strict ring sizes."""
    if EGO_POC_RING_LEN > 0:
        if oak_socket == PRIMARY_OAK_SOCKET:
            return EGO_POC_RING_LEN
        if oak_socket == DEPTH_OAK_SOCKET:
            return max(64, EGO_POC_RING_LEN // 4)
        return max(128, EGO_POC_RING_LEN // 2)
    if oak_socket == PRIMARY_OAK_SOCKET:
        return STRICT_PRIMARY_RING_LEN
    if oak_socket == DEPTH_OAK_SOCKET:
        return STRICT_DEPTH_RING_LEN
    return STRICT_RGB_RING_LEN


class RingOverflowStats:
    """Count samples dropped because a bounded deque was already full."""

    def __init__(self) -> None:
        self._parallel: Counter[str] = Counter()
        self._main: Counter[str] = Counter()

    def note_parallel_overflow(self, cam_name: str) -> None:
        self._parallel[cam_name] += 1

    def note_main_overflow(self, cam_name: str) -> None:
        self._main[cam_name] += 1

    def snapshot(self) -> tuple[dict[str, int], dict[str, int]]:
        return (
            {cam: int(count) for cam, count in self._parallel.items()},
            {cam: int(count) for cam, count in self._main.items()},
        )

    def delta_since(
        self,
        baseline: tuple[dict[str, int], dict[str, int]] | None,
    ) -> dict[str, int]:
        if baseline is None:
            parallel_base: dict[str, int] = {}
            main_base: dict[str, int] = {}
        else:
            parallel_base, main_base = baseline
        out: dict[str, int] = {}
        for cam in sorted(set(self._parallel) | set(self._main) | set(parallel_base) | set(main_base)):
            delta = (
                int(self._parallel.get(cam, 0))
                - int(parallel_base.get(cam, 0))
                + int(self._main.get(cam, 0))
                - int(main_base.get(cam, 0))
            )
            if delta:
                out[cam] = delta
        return out

    def report(self, *, prefix: str = "ingest_overflow_final") -> None:
        if not self._parallel and not self._main:
            return
        print(
            f"{prefix} parallel_ring={self._parallel.most_common(4)} "
            f"main_ring={self._main.most_common(4)}",
            flush=True,
        )


def append_bounded(
    ring: deque,
    sample: object,
    *,
    cam_name: str,
    overflow: RingOverflowStats | None,
    on_main: bool,
) -> None:
    if ring.maxlen and len(ring) >= int(ring.maxlen):
        if overflow is not None:
            if on_main:
                overflow.note_main_overflow(cam_name)
            else:
                overflow.note_parallel_overflow(cam_name)
    ring.append(sample)
