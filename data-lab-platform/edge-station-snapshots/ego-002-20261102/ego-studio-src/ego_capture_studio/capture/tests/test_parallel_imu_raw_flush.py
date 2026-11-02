"""Regression: parallel fsync_quad/device_tick must flush IMU before index reset."""

from __future__ import annotations

from types import SimpleNamespace

from imu_raw_flush import collect_imu_raw_since


def _sample_buf() -> SimpleNamespace:
    return SimpleNamespace(
        gyro_ts_ns=[100, 200, 300],
        gyro_xyz=[(1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (3.0, 0.0, 0.0)],
        accel_ts_ns=[150, 250],
        accel_xyz=[(0.0, 0.0, 9.8), (0.0, 0.0, 9.7)],
    )


def test_reset_before_flush_drops_all_imu_raw() -> None:
    """Bug pattern: drain loop reset indices to len(buf) without flushing first."""
    buf = _sample_buf()
    gyro_idx = len(buf.gyro_ts_ns)
    accel_idx = len(buf.accel_ts_ns)
    batch, _, _ = collect_imu_raw_since(buf, gyro_from=gyro_idx, accel_from=accel_idx)
    assert batch == []


def test_flush_before_reset_exports_imu_raw() -> None:
    """Fixed pattern matches sequential strict-sync drain loop."""
    buf = _sample_buf()
    batch, gyro_idx, accel_idx = collect_imu_raw_since(buf, gyro_from=0, accel_from=0)
    assert len(batch) == 5
    assert gyro_idx == len(buf.gyro_ts_ns)
    assert accel_idx == len(buf.accel_ts_ns)
    tail, _, _ = collect_imu_raw_since(buf, gyro_from=gyro_idx, accel_from=accel_idx)
    assert tail == []


def test_throttled_flush_every_third_commit_misses_early_frames() -> None:
    """Old burst path: flush only on commit % 3 left frames 1-2 without IMU raw."""
    buf = SimpleNamespace(
        gyro_ts_ns=[],
        gyro_xyz=[],
        accel_ts_ns=[],
        accel_xyz=[],
    )
    gyro_idx = 0
    accel_idx = 0
    per_commit_counts: list[int] = []
    for commit in range(1, 4):
        buf.gyro_ts_ns.append(100 * commit)
        buf.gyro_xyz.append((float(commit), 0.0, 0.0))
        if commit % 3 == 0:
            batch, gyro_idx, accel_idx = collect_imu_raw_since(
                buf, gyro_from=gyro_idx, accel_from=accel_idx
            )
            per_commit_counts.append(len(batch))
        else:
            per_commit_counts.append(0)
    assert per_commit_counts == [0, 0, 3]


def test_per_commit_drain_flush_exports_incremental_imu() -> None:
    """Burst commits must drain+flush on every frame, not only at loop start."""
    buf = SimpleNamespace(
        gyro_ts_ns=[],
        gyro_xyz=[],
        accel_ts_ns=[],
        accel_xyz=[],
    )
    gyro_idx = 0
    accel_idx = 0
    per_commit_counts: list[int] = []
    for commit in range(1, 4):
        buf.gyro_ts_ns.append(100 * commit)
        buf.gyro_xyz.append((float(commit), 0.0, 0.0))
        batch, gyro_idx, accel_idx = collect_imu_raw_since(
            buf, gyro_from=gyro_idx, accel_from=accel_idx
        )
        per_commit_counts.append(len(batch))
    assert per_commit_counts == [1, 1, 1]


def test_segment_imu_gate_blocks_until_samples_exist() -> None:
    from types import SimpleNamespace

    class _GateProbe:
        _segment_imu_align_margin_ns = staticmethod(lambda: 5_000_000)

        def __init__(self) -> None:
            self._segment_imu_gate = True

        @staticmethod
        def _first_imu_device_ts_ns(buf: SimpleNamespace) -> int | None:
            ts: list[int] = []
            if buf.gyro_ts_ns:
                ts.append(int(buf.gyro_ts_ns[0]))
            if buf.accel_ts_ns:
                ts.append(int(buf.accel_ts_ns[0]))
            return min(ts) if ts else None

        def _awaiting_segment_imu(
            self,
            buf: SimpleNamespace,
            *,
            primary_dev_ns: int | None = None,
        ) -> bool:
            if not self._segment_imu_gate:
                return False
            first_imu_ns = self._first_imu_device_ts_ns(buf)
            if first_imu_ns is None:
                return True
            if primary_dev_ns is None:
                return False
            return int(primary_dev_ns) + self._segment_imu_align_margin_ns() < int(first_imu_ns)

        def _release_segment_imu_gate(self) -> None:
            self._segment_imu_gate = False

    rec = _GateProbe()
    empty = SimpleNamespace(gyro_ts_ns=[], accel_ts_ns=[])
    ready = SimpleNamespace(gyro_ts_ns=[4_935_000_000], accel_ts_ns=[])
    stale_primary_ns = 3_835_000_000
    aligned_primary_ns = 4_936_500_000
    assert rec._awaiting_segment_imu(empty, primary_dev_ns=stale_primary_ns) is True
    assert rec._awaiting_segment_imu(ready, primary_dev_ns=stale_primary_ns) is True
    assert rec._awaiting_segment_imu(ready, primary_dev_ns=aligned_primary_ns) is False
    rec._release_segment_imu_gate()
    assert rec._awaiting_segment_imu(empty, primary_dev_ns=stale_primary_ns) is False
