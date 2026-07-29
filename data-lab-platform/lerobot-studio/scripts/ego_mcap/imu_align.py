"""IMU interpolation helpers (mirrors ego-stream-client/imu_align.py)."""

from __future__ import annotations

import numpy as np


def interpolate_linear(
    sample_ts: np.ndarray,
    sample_vals: np.ndarray,
    query_ts: int,
) -> np.ndarray:
    if sample_ts.size == 0:
        return np.zeros(sample_vals.shape[1], dtype=np.float32)
    ts = sample_ts.astype(np.int64)
    q = int(query_ts)
    if q <= int(ts[0]):
        return np.asarray(sample_vals[0], dtype=np.float32)
    if q >= int(ts[-1]):
        return np.asarray(sample_vals[-1], dtype=np.float32)
    idx = int(np.searchsorted(ts, q, side="right"))
    t0 = int(ts[idx - 1])
    t1 = int(ts[idx])
    v0 = np.asarray(sample_vals[idx - 1], dtype=np.float64)
    v1 = np.asarray(sample_vals[idx], dtype=np.float64)
    if t1 <= t0:
        return v0.astype(np.float32)
    w = (q - t0) / float(t1 - t0)
    return (v0 * (1.0 - w) + v1 * w).astype(np.float32)


def imu6_at_timestamp(
    gyro_ts: np.ndarray,
    gyro: np.ndarray,
    accel_ts: np.ndarray,
    accel: np.ndarray,
    t_rgb_ns: int,
    *,
    interpolate: bool = True,
) -> np.ndarray:
    g = interpolate_linear(gyro_ts, gyro, t_rgb_ns)
    a = interpolate_linear(accel_ts, accel, t_rgb_ns)
    return np.concatenate([g, a], axis=0).astype(np.float32)
