"""Parallel-drain POC entry point — does not modify record_oak_stream (main line)."""

from __future__ import annotations

import os
import time

import ego_capture_studio.cli.record_oak_stream as _record_main
from ego_capture_studio.capture.async_frame_writer import AsyncFrameWriter, writer_async_enabled
from ego_capture_studio.capture.capture_frame_profile import FrameProfiler, profile_enabled
from ego_capture_studio.capture.oak_4p_parallel_capture import Oak4pParallelEgoRecorder
from ego_capture_studio.capture.segment_store import SegmentCaptureWriter

# Swap recorder class for this CLI module only.
_record_main.Oak4pEgoRecorder = Oak4pParallelEgoRecorder  # type: ignore[misc]

_orig_append = _record_main._append_capture_frame
_orig_writer_close = SegmentCaptureWriter.close
_last_append_end: list[float] = [0.0]
_async_writer: AsyncFrameWriter | None = None


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


def _profiled_writer_append(*args, **kwargs) -> None:
    prof = FrameProfiler.get()
    t0 = time.perf_counter()
    _orig_append(*args, **kwargs)
    if prof is not None:
        prof.add("writer_append", time.perf_counter() - t0)


def _enqueue_capture_frame(*args, **kwargs) -> None:
    prof = FrameProfiler.get()
    t0 = time.perf_counter()
    if prof is not None and _last_append_end[0] > 0.0:
        prof.add("consumer_gap", t0 - _last_append_end[0])
    if _async_writer is not None:
        _async_writer.enqueue(*args, **kwargs)
    if prof is not None:
        prof.add("commit_enqueue", time.perf_counter() - t0)
        _last_append_end[0] = time.perf_counter()
        prof.tick_frames(1)


def _patched_writer_close(self: SegmentCaptureWriter) -> None:
    if _async_writer is not None:
        _async_writer.flush()
    _orig_writer_close(self)


def _install_async_writer() -> None:
    global _async_writer
    if not writer_async_enabled() or _async_writer is not None:
        return
    append_fn = _profiled_writer_append if profile_enabled() else _orig_append
    _async_writer = AsyncFrameWriter(append_fn)
    _async_writer.start()
    SegmentCaptureWriter.close = _patched_writer_close  # type: ignore[method-assign]
    _record_main._append_capture_frame = _enqueue_capture_frame  # type: ignore[method-assign]


def main() -> None:
    sync_mode = os.environ.get("EGO_CAPTURE_SYNC_MODE", "strict_grid").strip().lower()
    print(
        f"capture_backend=parallel_drain_poc sync_mode={sync_mode} "
        f"writer_async={int(writer_async_enabled())} "
        f"profile={int(profile_enabled())} branch=feat/ego-parallel-drain-poc",
        flush=True,
    )
    if writer_async_enabled():
        _install_async_writer()
    elif profile_enabled():
        _record_main._append_capture_frame = _profiled_append_capture_frame  # type: ignore[method-assign]
    try:
        _record_main.main()
    finally:
        prof = FrameProfiler.get()
        if prof is not None:
            prof.report(prefix="capture_profile_final")


if __name__ == "__main__":
    main()
