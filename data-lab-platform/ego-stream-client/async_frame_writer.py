"""Decouple fsync_quad commit from append_frame via a bounded frame queue (POC)."""

from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

EGO_CAPTURE_WRITER_ASYNC = os.environ.get("EGO_CAPTURE_WRITER_ASYNC", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
EGO_CAPTURE_FRAME_QUEUE_MAX = max(16, int(os.environ.get("EGO_CAPTURE_FRAME_QUEUE_MAX", "256")))


def writer_async_enabled() -> bool:
    return EGO_CAPTURE_WRITER_ASYNC


@dataclass(frozen=True)
class CaptureFramePacket:
    writer: Any
    preview_hub: Any
    recorder: Any
    timestamp_ns: int
    capture_out: dict
    preview_out: dict
    imu6: Any
    camera_ts_offset_ns: dict[str, int] | None
    primary_device_timestamp_ns: int | None
    imu_raw_batch: tuple | list | None


class AsyncFrameWriter:
    """Background thread drains CaptureFramePacket queue into append_frame."""

    def __init__(
        self,
        append_fn: Callable[..., None],
        *,
        maxsize: int = EGO_CAPTURE_FRAME_QUEUE_MAX,
        on_written: Callable[[], None] | None = None,
    ) -> None:
        self._append_fn = append_fn
        self._on_written = on_written
        self._queue: queue.Queue[CaptureFramePacket | None] = queue.Queue(maxsize=maxsize)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enqueued = 0
        self._written = 0
        self._drops = 0
        self._max_depth = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="ego-async-frame-writer",
            daemon=True,
        )
        self._thread.start()
        print(
            f"async_frame_writer_started queue_max={self._queue.maxsize}",
            flush=True,
        )

    def enqueue(
        self,
        writer: Any,
        preview_hub: Any,
        recorder: Any,
        *,
        timestamp_ns: int,
        capture_out: dict,
        preview_out: dict,
        imu6: Any,
        camera_ts_offset_ns: dict[str, int] | None = None,
        primary_device_timestamp_ns: int | None = None,
        imu_raw_batch: list | tuple | None = None,
    ) -> None:
        packet = CaptureFramePacket(
            writer=writer,
            preview_hub=preview_hub,
            recorder=recorder,
            timestamp_ns=int(timestamp_ns),
            capture_out=capture_out,
            preview_out=preview_out,
            imu6=imu6,
            camera_ts_offset_ns=camera_ts_offset_ns,
            primary_device_timestamp_ns=primary_device_timestamp_ns,
            imu_raw_batch=tuple(imu_raw_batch or ()),
        )
        while True:
            try:
                self._queue.put_nowait(packet)
                self._enqueued += 1
                self._max_depth = max(self._max_depth, self._queue.qsize())
                return
            except queue.Full:
                try:
                    dropped = self._queue.get_nowait()
                except queue.Empty:
                    return
                if dropped is None:
                    try:
                        self._queue.put_nowait(None)
                    except queue.Full:
                        pass
                    return
                self._drops += 1

    def flush(self, timeout_s: float = 120.0) -> None:
        if self._thread is None:
            return
        deadline = time.monotonic() + float(timeout_s)
        last_log = time.monotonic()
        pending = max(0, self._enqueued - self._written - self._drops)
        while self._queue.unfinished_tasks > 0 and time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_log >= 5.0:
                pending = max(0, self._enqueued - self._written - self._drops)
                print(
                    f"async_frame_writer_flush pending={pending} "
                    f"qsize={self._queue.qsize()} written={self._written}",
                    flush=True,
                )
                last_log = now
            time.sleep(0.002)
        pending = max(0, self._enqueued - self._written - self._drops)
        if self._queue.unfinished_tasks > 0 or pending > 0:
            print(
                f"async_frame_writer_flush_timeout pending={pending} "
                f"qsize={self._queue.qsize()} drops={self._drops}",
                flush=True,
            )
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=max(0.0, deadline - time.monotonic()))
        print(
            f"async_frame_writer_final enqueued={self._enqueued} written={self._written} "
            f"drops={self._drops} max_depth={self._max_depth} "
            f"pending={max(0, self._enqueued - self._written - self._drops)}",
            flush=True,
        )

    def _run(self) -> None:
        while True:
            if self._stop.is_set() and self._queue.empty():
                return
            try:
                packet = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if packet is None:
                self._queue.task_done()
                return
            try:
                self._append_fn(
                    packet.writer,
                    packet.preview_hub,
                    packet.recorder,
                    timestamp_ns=packet.timestamp_ns,
                    capture_out=packet.capture_out,
                    preview_out=packet.preview_out,
                    imu6=packet.imu6,
                    camera_ts_offset_ns=packet.camera_ts_offset_ns,
                    primary_device_timestamp_ns=packet.primary_device_timestamp_ns,
                    imu_raw_batch=packet.imu_raw_batch,
                )
            except Exception as exc:
                print(
                    f"async_frame_writer_append_error written={self._written} "
                    f"enqueued={self._enqueued} err={exc}",
                    flush=True,
                )
                raise
            self._written += 1
            if self._on_written is not None:
                self._on_written()
            self._queue.task_done()
