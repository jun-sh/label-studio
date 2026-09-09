"""Parallel-drain POC entry point — does not modify record_oak_stream (main line)."""

from __future__ import annotations

import os
import time

import ego_capture_studio.cli.record_oak_stream as _record_main
from ego_capture_studio.capture.capture_frame_profile import FrameProfiler, profile_enabled
from ego_capture_studio.capture.oak_4p_parallel_capture import Oak4pParallelEgoRecorder

# Swap recorder class for this CLI module only.
_record_main.Oak4pEgoRecorder = Oak4pParallelEgoRecorder  # type: ignore[misc]

_orig_append = _record_main._append_capture_frame
_last_append_end: list[float] = [0.0]


def _profiled_append_capture_frame(*args, **kwargs) -> None:
    prof = FrameProfiler.get()
    t0 = time.perf_counter()
    if prof is not None and _last_append_end[0] > 0.0:
        prof.add("consumer_gap", t0 - _last_append_end[0])
    _orig_append(*args, **kwargs)
    t1 = time.perf_counter()
    if prof is not None:
        prof.add("persist_append", t1 - t0)
        _last_append_end[0] = t1
        prof.tick_frames(1)


if profile_enabled():
    _record_main._append_capture_frame = _profiled_append_capture_frame  # type: ignore[method-assign]


def main() -> None:
    sync_mode = os.environ.get("EGO_CAPTURE_SYNC_MODE", "strict_grid").strip().lower()
    print(
        f"capture_backend=parallel_drain_poc sync_mode={sync_mode} "
        f"profile={int(profile_enabled())} branch=feat/ego-parallel-drain-poc",
        flush=True,
    )
    try:
        _record_main.main()
    finally:
        prof = FrameProfiler.get()
        if prof is not None:
            prof.report(prefix="capture_profile_final")


if __name__ == "__main__":
    main()
