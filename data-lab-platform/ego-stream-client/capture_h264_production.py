"""Production H264 capture backend: parallel USB drain + fsync_quad + async writer."""

from __future__ import annotations

import os
import time
from typing import Any

from ego_capture_studio.capture.async_frame_writer import AsyncFrameWriter, writer_async_enabled
from ego_capture_studio.capture.capture_frame_profile import FrameProfiler, profile_enabled
from ego_capture_studio.capture.oak_4p_parallel_capture import Oak4pParallelEgoRecorder
from ego_capture_studio.capture.segment_store import SegmentCaptureWriter

CAPTURE_BACKEND_VERSION = "h264-fsync-quad-v0.2.2"


def capture_sync_mode() -> str:
    return os.environ.get("EGO_CAPTURE_SYNC_MODE", "").strip().lower()


def parallel_drain_enabled() -> bool:
    flag = os.environ.get("EGO_CAPTURE_PARALLEL_DRAIN", "").strip().lower()
    if flag in ("0", "false", "no"):
        return False
    if flag in ("1", "true", "yes"):
        return True
    return capture_sync_mode() in ("fsync_quad", "device_tick")


def h264_production_backend_enabled() -> bool:
    return parallel_drain_enabled()


def install_h264_production_backend(record_module: Any) -> bool:
    """Swap Oak4pEgoRecorder + optional async writer on the record CLI module."""
    if not h264_production_backend_enabled():
        return False

    sync_mode = capture_sync_mode() or "fsync_quad"
    print(
        f"capture_backend={CAPTURE_BACKEND_VERSION} parallel_drain=1 "
        f"sync_mode={sync_mode} writer_async={int(writer_async_enabled())} "
        f"profile={int(profile_enabled())}",
        flush=True,
    )

    record_module.Oak4pEgoRecorder = Oak4pParallelEgoRecorder  # type: ignore[misc]

    orig_append = record_module._append_capture_frame
    orig_writer_close = SegmentCaptureWriter.close
    last_append_end: list[float] = [0.0]
    async_writer: AsyncFrameWriter | None = None

    def _profiled_append_capture_frame(*args, **kwargs) -> None:
        prof = FrameProfiler.get()
        t0 = time.perf_counter()
        if prof is not None and last_append_end[0] > 0.0:
            prof.add("consumer_gap", t0 - last_append_end[0])
        orig_append(*args, **kwargs)
        t1 = time.perf_counter()
        if prof is not None:
            prof.add("persist_append", t1 - t0)
            last_append_end[0] = t1
            prof.tick_frames(1)

    def _profiled_writer_append(*args, **kwargs) -> None:
        prof = FrameProfiler.get()
        t0 = time.perf_counter()
        orig_append(*args, **kwargs)
        if prof is not None:
            prof.add("writer_append", time.perf_counter() - t0)

    def _enqueue_capture_frame(*args, **kwargs) -> None:
        prof = FrameProfiler.get()
        t0 = time.perf_counter()
        if prof is not None and last_append_end[0] > 0.0:
            prof.add("consumer_gap", t0 - last_append_end[0])
        if async_writer is not None:
            async_writer.enqueue(*args, **kwargs)
        if prof is not None:
            prof.add("commit_enqueue", time.perf_counter() - t0)
            last_append_end[0] = time.perf_counter()
            prof.tick_frames(1)

    def _patched_writer_close(self: SegmentCaptureWriter) -> None:
        if async_writer is not None:
            async_writer.flush()
        orig_writer_close(self)

    def _flush_async_capture_queue() -> None:
        if async_writer is not None:
            async_writer.flush()

    record_module._flush_async_capture_queue = _flush_async_capture_queue  # type: ignore[attr-defined]

    if writer_async_enabled():
        append_fn = _profiled_writer_append if profile_enabled() else orig_append
        async_writer = AsyncFrameWriter(append_fn)
        async_writer.start()
        SegmentCaptureWriter.close = _patched_writer_close  # type: ignore[method-assign]
        record_module._append_capture_frame = _enqueue_capture_frame  # type: ignore[method-assign]
    elif profile_enabled():
        record_module._append_capture_frame = _profiled_append_capture_frame  # type: ignore[method-assign]

    return True
