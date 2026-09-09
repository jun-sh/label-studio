"""Unit tests for parallel-drain ring buffers (no OAK hardware)."""

from __future__ import annotations

from collections import deque

from ego_capture_studio.capture.oak_4p_parallel_capture import (
    _ParallelImuAccumulator,
    _ThreadSafeCamRing,
)
from ego_capture_studio.capture.oak_4p_capture import _CamRingSample


def test_thread_safe_cam_ring_drain_to_target() -> None:
    ring = _ThreadSafeCamRing(maxlen=8)
    target: deque[_CamRingSample] = deque(maxlen=8)
    ring.append(_CamRingSample(100, b"a"))
    ring.append(_CamRingSample(200, b"b"))
    moved = ring.drain_to(target, limit=10)
    assert moved == 2
    assert len(target) == 2
    assert target[0].ts_ns == 100
    assert ring.depth == 0
    assert ring.total_appends == 2


def test_parallel_imu_accumulator_merge_clears_buffer() -> None:
    acc = _ParallelImuAccumulator()

    class _FakeImuMsg:
        packets = ()

    class _Pkt:
        def __init__(self) -> None:
            self.acceleroMeter = _Vec()
            self.gyroscope = _Vec()

    class _Vec:
        x = 1.0
        y = 2.0
        z = 3.0

        def getTimestampDevice(self):
            class _Ts:
                def total_seconds(self):
                    return 0.001

            return _Ts()

    msg = _FakeImuMsg()
    msg.packets = (_Pkt(),)
    acc.ingest_packet(msg)

    class _Buf:
        accel_ts_ns: list[int] = []
        accel_xyz: list[tuple[float, float, float]] = []
        gyro_ts_ns: list[int] = []
        gyro_xyz: list[tuple[float, float, float]] = []

    buf = _Buf()
    acc.merge_into(buf)
    assert len(buf.gyro_ts_ns) == 1
    assert buf.gyro_xyz[0] == (1.0, 2.0, 3.0)
    buf2 = _Buf()
    acc.merge_into(buf2)
    assert len(buf2.gyro_ts_ns) == 0
