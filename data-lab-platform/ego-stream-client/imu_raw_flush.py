"""Flush high-rate IMU samples from EpisodeBuffers into imu_raw.jsonl records."""

from __future__ import annotations

from typing import Any


def imu_raw_record(*, ts_ns: int, sensor: str, x: float, y: float, z: float) -> dict[str, Any]:
    if sensor not in ("accel", "gyro"):
        raise ValueError(f"invalid imu sensor: {sensor}")
    return {
        "ts_ns": int(ts_ns),
        "sensor": sensor,
        "x": float(x),
        "y": float(y),
        "z": float(z),
    }


def collect_imu_raw_since(
    buf: Any,
    *,
    gyro_from: int,
    accel_from: int,
) -> tuple[list[dict[str, Any]], int, int]:
    """Return new accel/gyro rows since last flush indices (lists may be trimmed upstream)."""
    records: list[dict[str, Any]] = []
    gyro_ts = list(getattr(buf, "gyro_ts_ns", []) or [])
    gyro_xyz = list(getattr(buf, "gyro_xyz", []) or [])
    accel_ts = list(getattr(buf, "accel_ts_ns", []) or [])
    accel_xyz = list(getattr(buf, "accel_xyz", []) or [])

    new_gyro = max(0, min(gyro_from, len(gyro_ts)))
    new_accel = max(0, min(accel_from, len(accel_ts)))

    for i in range(new_gyro, len(gyro_ts)):
        x, y, z = gyro_xyz[i]
        records.append(imu_raw_record(ts_ns=int(gyro_ts[i]), sensor="gyro", x=x, y=y, z=z))
    for i in range(new_accel, len(accel_ts)):
        x, y, z = accel_xyz[i]
        records.append(imu_raw_record(ts_ns=int(accel_ts[i]), sensor="accel", x=x, y=y, z=z))

    records.sort(key=lambda r: (int(r["ts_ns"]), r["sensor"]))
    return records, len(gyro_ts), len(accel_ts)
