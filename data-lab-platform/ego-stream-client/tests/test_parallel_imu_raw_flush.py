"""Regression: parallel fsync_quad/device_tick must flush IMU before index reset."""

from __future__ import annotations

from types import SimpleNamespace

from ego_capture_studio.capture.imu_raw_flush import collect_imu_raw_since


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
