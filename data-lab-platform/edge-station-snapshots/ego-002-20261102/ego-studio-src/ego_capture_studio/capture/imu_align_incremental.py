"""Incremental IMU alignment without per-frame full-buffer numpy copies (POC)."""

from __future__ import annotations

import bisect
from typing import Sequence

import numpy as np

from ego_capture_studio.capture.buffers import EpisodeBuffers
from ego_capture_studio.capture.imu_align import imu6_at_timestamp


def _nearest_from_lists(
    ts_list: Sequence[int],
    val_list: Sequence[tuple[float, float, float]],
    query_ts: int,
) -> tuple[float, float, float]:
    if not ts_list:
        return (0.0, 0.0, 0.0)
    q = int(query_ts)
    idx = min(range(len(ts_list)), key=lambda i: abs(int(ts_list[i]) - q))
    return val_list[idx]


def _interp_from_lists(
    ts_list: Sequence[int],
    val_list: Sequence[tuple[float, float, float]],
    query_ts: int,
) -> tuple[float, float, float]:
    if not ts_list:
        return (0.0, 0.0, 0.0)
    q = int(query_ts)
    if q <= int(ts_list[0]):
        return val_list[0]
    if q >= int(ts_list[-1]):
        return val_list[-1]
    idx = bisect.bisect_right(ts_list, q)
    t0 = int(ts_list[idx - 1])
    t1 = int(ts_list[idx])
    v0 = val_list[idx - 1]
    v1 = val_list[idx]
    if t1 <= t0:
        return v0
    w = (q - t0) / float(t1 - t0)
    return (
        v0[0] * (1.0 - w) + v1[0] * w,
        v0[1] * (1.0 - w) + v1[1] * w,
        v0[2] * (1.0 - w) + v1[2] * w,
    )


def imu6_at_timestamp_from_buf(
    buf: EpisodeBuffers,
    t_rgb_ns: int,
    *,
    interpolate: bool = False,
) -> np.ndarray:
    """Query IMU at t_rgb_ns using list bisect (no np.asarray of full IMU history)."""
    if interpolate:
        g = _interp_from_lists(buf.gyro_ts_ns, buf.gyro_xyz, t_rgb_ns)
        a = _interp_from_lists(buf.accel_ts_ns, buf.accel_xyz, t_rgb_ns)
    else:
        g = _nearest_from_lists(buf.gyro_ts_ns, buf.gyro_xyz, t_rgb_ns)
        a = _nearest_from_lists(buf.accel_ts_ns, buf.accel_xyz, t_rgb_ns)
    return np.array([g[0], g[1], g[2], a[0], a[1], a[2]], dtype=np.float32)


def imu6_for_frame(
    buf: EpisodeBuffers,
    t_rgb_ns: int,
    *,
    interpolate: bool = False,
    incremental: bool = True,
) -> np.ndarray:
    if incremental:
        return imu6_at_timestamp_from_buf(buf, t_rgb_ns, interpolate=interpolate)
    from ego_capture_studio.capture.lerobot_episode import _buffers_to_numpy

    g_ts, g, a_ts, a = _buffers_to_numpy(buf)
    return imu6_at_timestamp(g_ts, g, a_ts, a, t_rgb_ns, interpolate=interpolate)
