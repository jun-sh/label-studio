"""Parity: incremental IMU align vs full numpy path."""

from __future__ import annotations

import numpy as np

import tests.conftest  # noqa: F401 — stubs

from ego_capture_studio.capture.buffers import EpisodeBuffers
from ego_capture_studio.capture.imu_align_incremental import imu6_for_frame
from ego_capture_studio.capture.lerobot_episode import _buffers_to_numpy
from ego_capture_studio.capture.imu_align import imu6_at_timestamp


def _fill_buf(n: int = 400) -> EpisodeBuffers:
    buf = EpisodeBuffers()
    for i in range(n):
        ts = i * 2_500_000
        buf.gyro_ts_ns.append(ts)
        buf.gyro_xyz.append((float(i), float(i) + 1, float(i) + 2))
        buf.accel_ts_ns.append(ts + 100_000)
        buf.accel_xyz.append((float(i) * 0.1, float(i) * 0.2, float(i) * 0.3))
    return buf


def test_imu6_incremental_matches_numpy_interpolate() -> None:
    buf = _fill_buf()
    query = 500_000_000
    inc = imu6_for_frame(buf, query, interpolate=True, incremental=True)
    g_ts, g, a_ts, a = _buffers_to_numpy(buf)
    ref = imu6_at_timestamp(g_ts, g, a_ts, a, query, interpolate=True)
    np.testing.assert_allclose(inc, ref, rtol=1e-5, atol=1e-5)


def test_imu6_incremental_matches_numpy_nearest() -> None:
    buf = _fill_buf()
    query = 500_123_456
    inc = imu6_for_frame(buf, query, interpolate=False, incremental=True)
    g_ts, g, a_ts, a = _buffers_to_numpy(buf)
    ref = imu6_at_timestamp(g_ts, g, a_ts, a, query, interpolate=False)
    np.testing.assert_allclose(inc, ref, rtol=1e-5, atol=1e-5)
