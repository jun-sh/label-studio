"""Tests for imu_raw.jsonl flush helpers."""

from __future__ import annotations

from types import SimpleNamespace

from imu_raw_flush import collect_imu_raw_since, imu_raw_record


def test_collect_imu_raw_since_incremental() -> None:
    buf = SimpleNamespace(
        gyro_ts_ns=[100, 200],
        gyro_xyz=[(1.0, 0.0, 0.0), (2.0, 0.0, 0.0)],
        accel_ts_ns=[150],
        accel_xyz=[(0.0, 0.0, 9.8)],
    )
    batch, g_idx, a_idx = collect_imu_raw_since(buf, gyro_from=0, accel_from=0)
    assert len(batch) == 3
    assert batch[0]["sensor"] == "gyro"
    assert batch[0]["ts_ns"] == 100
    assert g_idx == 2 and a_idx == 1

    batch2, g_idx2, a_idx2 = collect_imu_raw_since(buf, gyro_from=g_idx, accel_from=a_idx)
    assert batch2 == []
    assert g_idx2 == 2 and a_idx2 == 1


def test_imu_raw_record_validation() -> None:
    rec = imu_raw_record(ts_ns=1, sensor="gyro", x=0.1, y=0.2, z=0.3)
    assert rec["sensor"] == "gyro"
    assert rec["x"] == 0.1
