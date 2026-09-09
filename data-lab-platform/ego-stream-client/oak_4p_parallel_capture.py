"""Parallel USB drain POC — isolated from oak_4p_capture.py (main line).

Four per-camera drain threads + IMU drain thread feed thread-safe rings; the main
thread commits frames via strict grid (default) or device-tick mode (POC).

Enable via ecs-record-oak-mcap-parallel-drain.service (feat/ego-parallel-drain-poc).
Set EGO_CAPTURE_SYNC_MODE=device_tick for primary-driven commit (no wall-clock grid wait).
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Callable

import numpy as np

from ego_capture_studio.capture.oak_4p_capture import (
    EGO_FRAME_INTERVAL_MS,
    EGO_IMU_INTERPOLATE,
    EGO_STRICT_SYNC_PREVIEW_DRAIN,
    OAK_H264_SEQUENTIAL,
    Oak4pEgoRecorder,
    STRICT_IMU_BUFFER_MAX,
    _CamRingSample,
    _device_ts_ns,
    _frame_from_packet,
    _jpeg_from_packet,
    oak_hw_preview_h264_enabled,
)

EGO_CAPTURE_SYNC_MODE = os.environ.get("EGO_CAPTURE_SYNC_MODE", "strict_grid").strip().lower()
EGO_IMU_INCREMENTAL = os.environ.get("EGO_IMU_INCREMENTAL", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
_DEVICE_TICK_IDLE_SLEEP_S = max(0.0, float(os.environ.get("EGO_DEVICE_TICK_IDLE_SLEEP_US", "50")) / 1e6)
_DEVICE_TICK_BURST_MAX = max(1, int(os.environ.get("EGO_DEVICE_TICK_BURST_MAX", "8")))

_PARALLEL_DRAIN_BATCH = max(8, int(os.environ.get("EGO_PARALLEL_DRAIN_BATCH", "128")))
_PARALLEL_IDLE_SLEEP_S = max(0.0, float(os.environ.get("EGO_PARALLEL_IDLE_SLEEP_US", "100")) / 1e6)
_PARALLEL_SPIN_ROUNDS = max(1, int(os.environ.get("EGO_PARALLEL_SPIN_ROUNDS", "64")))


class _ThreadSafeCamRing:
    """Per-camera ring filled by a dedicated drain thread."""

    def __init__(self, maxlen: int) -> None:
        self._maxlen = int(maxlen)
        self._deque: deque[_CamRingSample] = deque(maxlen=self._maxlen)
        self._lock = threading.Lock()
        self._appends = 0

    def append(self, sample: _CamRingSample) -> None:
        with self._lock:
            self._deque.append(sample)
            self._appends += 1

    def drain_to(self, target: deque[_CamRingSample], limit: int = _PARALLEL_DRAIN_BATCH) -> int:
        moved = 0
        with self._lock:
            while self._deque and moved < limit:
                target.append(self._deque.popleft())
                moved += 1
        return moved

    @property
    def depth(self) -> int:
        with self._lock:
            return len(self._deque)

    @property
    def total_appends(self) -> int:
        with self._lock:
            return self._appends


class _ParallelImuAccumulator:
    """IMU samples collected off the strict-sync hot path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.accel_ts_ns: list[int] = []
        self.accel_xyz: list[tuple[float, float, float]] = []
        self.gyro_ts_ns: list[int] = []
        self.gyro_xyz: list[tuple[float, float, float]] = []

    def ingest_packet(self, imu_msg: Any) -> None:
        accel_batch: list[tuple[int, tuple[float, float, float]]] = []
        gyro_batch: list[tuple[int, tuple[float, float, float]]] = []
        for pkt in imu_msg.packets:
            accel = pkt.acceleroMeter
            gyro = pkt.gyroscope
            accel_batch.append(
                (_device_ts_ns(accel.getTimestampDevice()), (float(accel.x), float(accel.y), float(accel.z)))
            )
            gyro_batch.append(
                (_device_ts_ns(gyro.getTimestampDevice()), (float(gyro.x), float(gyro.y), float(gyro.z)))
            )
        if not accel_batch and not gyro_batch:
            return
        with self._lock:
            for ts, xyz in accel_batch:
                self.accel_ts_ns.append(ts)
                self.accel_xyz.append(xyz)
            for ts, xyz in gyro_batch:
                self.gyro_ts_ns.append(ts)
                self.gyro_xyz.append(xyz)
            overflow = len(self.gyro_ts_ns) - STRICT_IMU_BUFFER_MAX
            if overflow > 0:
                del self.gyro_ts_ns[:overflow]
                del self.gyro_xyz[:overflow]
                del self.accel_ts_ns[:overflow]
                del self.accel_xyz[:overflow]

    def merge_into(self, buf: Any) -> None:
        with self._lock:
            if not self.gyro_ts_ns:
                return
            buf.accel_ts_ns.extend(self.accel_ts_ns)
            buf.accel_xyz.extend(self.accel_xyz)
            buf.gyro_ts_ns.extend(self.gyro_ts_ns)
            buf.gyro_xyz.extend(self.gyro_xyz)
            self.accel_ts_ns.clear()
            self.accel_xyz.clear()
            self.gyro_ts_ns.clear()
            self.gyro_xyz.clear()


class Oak4pParallelEgoRecorder(Oak4pEgoRecorder):
    """Oak4pEgoRecorder with per-camera + IMU parallel USB drain threads."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._drain_stop = threading.Event()
        self._drain_threads: list[threading.Thread] = []
        self._parallel_rings: dict[str, _ThreadSafeCamRing] = {}
        self._parallel_imu = _ParallelImuAccumulator()
        self._parallel_preview_lock = threading.Lock()
        self._parallel_preview_last: dict[str, bytes | np.ndarray] = {}
        self._parallel_preview_hw = False
        self._straggler_diag: Any = None

    def connect(self) -> None:
        super().connect()
        self._start_parallel_drains()
        cam_depths = {oak: self._parallel_rings[oak].depth for oak in self._cam_list}
        print(
            f"parallel_drain_started cams={list(self._cam_list)} "
            f"threads={len(self._drain_threads)} ring_depth={cam_depths}",
            flush=True,
        )

    def stop(self) -> None:
        if self._straggler_diag is not None:
            self._straggler_diag.report(prefix="straggler_final")
        self._stop_parallel_drains()
        super().stop()

    def _start_parallel_drains(self) -> None:
        self._drain_stop.clear()
        self._parallel_rings = {
            oak: _ThreadSafeCamRing(self._strict_ring_len(oak)) for oak in self._cam_list
        }
        for cam_name, queue in self._cam_queues.items():
            ring = self._parallel_rings[cam_name]
            thread = threading.Thread(
                target=self._cam_drain_worker,
                args=(cam_name, queue, ring),
                name=f"ego-cam-drain-{cam_name}",
                daemon=True,
            )
            thread.start()
            self._drain_threads.append(thread)

        if self._imu_queue is not None:
            thread = threading.Thread(
                target=self._imu_drain_worker,
                name="ego-imu-drain",
                daemon=True,
            )
            thread.start()
            self._drain_threads.append(thread)

        self._parallel_preview_hw = bool(
            self._hw_jpeg or (self._hw_h264 and oak_hw_preview_h264_enabled())
        )
        if self._preview_queues and not EGO_STRICT_SYNC_PREVIEW_DRAIN:
            thread = threading.Thread(
                target=self._preview_drain_worker,
                name="ego-preview-drain",
                daemon=True,
            )
            thread.start()
            self._drain_threads.append(thread)

    def _stop_parallel_drains(self) -> None:
        self._drain_stop.set()
        for thread in self._drain_threads:
            thread.join(timeout=2.5)
        self._drain_threads.clear()
        self._parallel_rings.clear()

    def _cam_drain_worker(self, cam_name: str, queue: Any, ring: _ThreadSafeCamRing) -> None:
        hw_jpeg = self._hw_jpeg
        hw_h264 = self._hw_h264
        idle_rounds = 0
        while not self._drain_stop.is_set():
            drained = 0
            for _ in range(_PARALLEL_SPIN_ROUNDS):
                pkt = queue.tryGet()
                if pkt is None:
                    break
                while pkt is not None:
                    ts = _device_ts_ns(pkt.getTimestampDevice())
                    if hw_jpeg or hw_h264:
                        payload = _jpeg_from_packet(pkt)
                    else:
                        payload = _frame_from_packet(pkt)
                    if payload is not None:
                        ring.append(_CamRingSample(ts, payload))
                    drained += 1
                    pkt = queue.tryGet()
            if drained:
                idle_rounds = 0
                continue
            idle_rounds += 1
            if idle_rounds < 8:
                continue
            time.sleep(_PARALLEL_IDLE_SLEEP_S)

    def _imu_drain_worker(self) -> None:
        while not self._drain_stop.is_set():
            queue = self._imu_queue
            if queue is None:
                return
            try:
                batches = list(queue.tryGetAll())
            except RuntimeError:
                time.sleep(0.001)
                continue
            if not batches:
                time.sleep(_PARALLEL_IDLE_SLEEP_S)
                continue
            for imu_msg in batches:
                self._parallel_imu.ingest_packet(imu_msg)

    def _preview_drain_worker(self) -> None:
        while not self._drain_stop.is_set():
            drained = False
            for cam_name, queue in self._preview_queues.items():
                pkt = queue.tryGet()
                while pkt is not None:
                    drained = True
                    if self._parallel_preview_hw:
                        payload = _jpeg_from_packet(pkt)
                    else:
                        payload = _frame_from_packet(pkt)
                    if payload is not None:
                        with self._parallel_preview_lock:
                            self._parallel_preview_last[cam_name] = payload
                    pkt = queue.tryGet()
            if not drained:
                time.sleep(_PARALLEL_IDLE_SLEEP_S)

    def _drain_cam_queues_to_rings(
        self,
        cam_rings: dict[str, deque[_CamRingSample]],
    ) -> None:
        for cam_name in self._cam_list:
            ring = self._parallel_rings.get(cam_name)
            if ring is not None:
                ring.drain_to(cam_rings[cam_name])

    def _drain_imu(self, buf: Any) -> None:
        self._parallel_imu.merge_into(buf)

    def _drain_preview_queues(self) -> dict[str, bytes] | dict[str, np.ndarray]:
        if EGO_STRICT_SYNC_PREVIEW_DRAIN:
            return super()._drain_preview_queues()
        with self._parallel_preview_lock:
            if not self._parallel_preview_last:
                return {}
            return dict(self._parallel_preview_last)

    def parallel_drain_stats(self) -> dict[str, Any]:
        rings = {
            oak: {
                "depth": self._parallel_rings[oak].depth,
                "appends": self._parallel_rings[oak].total_appends,
            }
            for oak in self._cam_list
            if oak in self._parallel_rings
        }
        return {
            "threads": len(self._drain_threads),
            "rings": rings,
            "sync_mode": EGO_CAPTURE_SYNC_MODE,
        }

    def iter_strict_sync_frames(
        self,
        duration_s: float,
        *,
        interval_ms: int | None = None,
        imu_interpolate: bool | None = None,
        grid_epoch_ns: int = 0,
        shutdown_check: Callable[[], bool] | None = None,
    ):
        if EGO_CAPTURE_SYNC_MODE == "device_tick":
            yield from self.iter_device_tick_frames(
                duration_s,
                interval_ms=interval_ms,
                imu_interpolate=imu_interpolate,
                grid_epoch_ns=grid_epoch_ns,
                shutdown_check=shutdown_check,
            )
            return
        yield from super().iter_strict_sync_frames(
            duration_s,
            interval_ms=interval_ms,
            imu_interpolate=imu_interpolate,
            grid_epoch_ns=grid_epoch_ns,
            shutdown_check=shutdown_check,
        )

    def iter_device_tick_frames(
        self,
        duration_s: float,
        *,
        interval_ms: int | None = None,
        imu_interpolate: bool | None = None,
        grid_epoch_ns: int = 0,
        shutdown_check: Callable[[], bool] | None = None,
    ):
        """Device-driven commit: pop when rings are ready, no wall-clock grid wait.

        Timestamps stay on a uniform interval_ms grid (+33ms) for commercial gates.
        Bursts up to EGO_DEVICE_TICK_BURST_MAX frames per drain cycle to drain backlog.
        """
        if self._device is None:
            raise RuntimeError("Call connect() first")

        from ego_capture_studio.capture.buffers import EpisodeBuffers
        from ego_capture_studio.capture.camera_map import OAK_SOCKET_TO_LEROBOT_VIDEO, PRIMARY_OAK_SOCKET
        from ego_capture_studio.capture.imu_align_incremental import imu6_for_frame
        from ego_capture_studio.capture.straggler_diag import StragglerDiagnostics, straggler_log_enabled

        ms = int(interval_ms if interval_ms is not None else EGO_FRAME_INTERVAL_MS)
        interval_ns = int(ms) * 1_000_000
        use_imu_interp = EGO_IMU_INTERPOLATE if imu_interpolate is None else bool(imu_interpolate)

        self._imu_flush_gyro_idx = 0
        self._imu_flush_accel_idx = 0
        self._pending_imu_raw = []

        buf = EpisodeBuffers()
        self._strict_imu_buf = buf
        cam_rings: dict[str, deque[_CamRingSample]] = {
            oak: deque(maxlen=self._strict_ring_len(oak)) for oak in self._cam_list
        }
        last_preview_oak: dict[str, bytes] | dict[str, np.ndarray] = {}
        t_end = time.monotonic() + float(duration_s)
        last_emit_ts_ns: int | None = self._strict_last_emit_ts_ns
        imu_flush_tick = 0

        from ego_capture_studio.capture.capture_frame_profile import FrameProfiler

        prof = FrameProfiler.get()

        self._straggler_diag = StragglerDiagnostics() if straggler_log_enabled() else None

        print(
            f"device_tick_sync interval_ms={ms} burst_max={_DEVICE_TICK_BURST_MAX} "
            f"h264_sequential={int(self._hw_h264 and OAK_H264_SEQUENTIAL)} "
            f"imu_incremental={int(EGO_IMU_INCREMENTAL)} straggler_log={int(straggler_log_enabled())}",
            flush=True,
        )

        def _commit_one() -> tuple[int, dict, dict, Any, dict] | None:
            nonlocal last_emit_ts_ns, imu_flush_tick
            t_commit = time.perf_counter()
            pre_depths = {oak: len(cam_rings[oak]) for oak in self._cam_list}
            if self._hw_h264 and OAK_H264_SEQUENTIAL:
                seq_sample = self._yield_h264_sequential_sample(cam_rings)
                if seq_sample is None:
                    return None
                capture_out, offsets, primary_ts_ns = seq_sample
            else:
                primary_ring = cam_rings[PRIMARY_OAK_SOCKET]
                if not primary_ring:
                    return None
                primary_sample = primary_ring.popleft()
                primary_ts_ns = int(primary_sample.ts_ns)
                capture_out = {}
                offsets = {}
                for oak in self._cam_list:
                    lerobot_key = OAK_SOCKET_TO_LEROBOT_VIDEO[oak]
                    if oak == PRIMARY_OAK_SOCKET:
                        sample = primary_sample
                    else:
                        sample = self._ring_sample_locked_to_primary(
                            cam_rings[oak], primary_ts_ns
                        )
                    if sample is None:
                        return None
                    offsets[lerobot_key] = int(sample.ts_ns) - primary_ts_ns
                    capture_out[lerobot_key] = sample.payload
            if len(capture_out) < len(self._cam_list):
                return None

            if last_emit_ts_ns is None:
                if int(grid_epoch_ns) > 0:
                    t_emit_ns = int(grid_epoch_ns)
                else:
                    t_emit_ns = self._align_epoch_to_device(int(primary_ts_ns), interval_ns)
                self._strict_grid_epoch_ns = int(t_emit_ns)
            else:
                t_emit_ns = int(last_emit_ts_ns) + interval_ns

            imu_flush_tick += 1
            if imu_flush_tick % 3 == 0:
                self._flush_imu_raw_from_buf(buf)
            t_imu = time.perf_counter()
            imu6 = imu6_for_frame(
                buf,
                t_emit_ns,
                interpolate=use_imu_interp,
                incremental=EGO_IMU_INCREMENTAL,
            )
            if prof is not None:
                prof.add("imu_query", time.perf_counter() - t_imu)
            if self._straggler_diag is not None:
                self._straggler_diag.note_pre_commit_depths(pre_depths, offsets)
            preview_out = {
                OAK_SOCKET_TO_LEROBOT_VIDEO[oak]: last_preview_oak[oak]
                for oak in self._cam_list
                if oak in last_preview_oak
            }
            last_emit_ts_ns = int(t_emit_ns)
            self._strict_last_emit_ts_ns = last_emit_ts_ns
            if prof is not None:
                prof.add("commit_pop_imu", time.perf_counter() - t_commit)
            return int(t_emit_ns), capture_out, preview_out, imu6, offsets

        while time.monotonic() < t_end:
            if shutdown_check and shutdown_check():
                return
            t_iter = time.perf_counter()
            t_drain = time.perf_counter()
            self._drain_imu(buf)
            self._trim_imu_buffer(buf)
            self._imu_flush_gyro_idx = len(buf.gyro_ts_ns)
            self._imu_flush_accel_idx = len(buf.accel_ts_ns)
            self._drain_cam_queues_to_rings(cam_rings)

            if not EGO_STRICT_SYNC_PREVIEW_DRAIN:
                preview = self._drain_preview_queues()
                if preview:
                    last_preview_oak.update(preview)
            drain_s = time.perf_counter() - t_drain

            burst = 0
            while burst < _DEVICE_TICK_BURST_MAX and all(cam_rings[oak] for oak in self._cam_list):
                row = _commit_one()
                if row is None:
                    break
                burst += 1
                yield row
                if shutdown_check and shutdown_check():
                    return
                if time.monotonic() >= t_end:
                    return

            if prof is not None and burst > 0:
                prof.add("drain_xfer", drain_s, frames=burst)
            if burst == 0:
                if self._straggler_diag is not None:
                    self._straggler_diag.note_wait_empty(cam_rings, list(self._cam_list))
                t_idle = time.perf_counter()
                time.sleep(_DEVICE_TICK_IDLE_SLEEP_S)
                idle_s = time.perf_counter() - t_idle
                if prof is not None:
                    prof.add("idle_sleep", idle_s)
                    spin_s = max(0.0, time.perf_counter() - t_iter - drain_s - idle_s)
                    if spin_s > 0.0:
                        prof.add("spin_wait", spin_s)
