"""LeRobot episode helpers for OAK capture."""

from __future__ import annotations

import numpy as np

from ego_capture_studio.capture.buffers import EpisodeBuffers


def identity_pose_xyzw() -> np.ndarray:
    return np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0], dtype=np.float64)


def _buffers_to_numpy(buf: EpisodeBuffers) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    gyro_ts = np.asarray(buf.gyro_ts_ns, dtype=np.int64)
    gyro = np.asarray(buf.gyro_xyz, dtype=np.float32)
    accel_ts = np.asarray(buf.accel_ts_ns, dtype=np.int64)
    accel = np.asarray(buf.accel_xyz, dtype=np.float32)
    if gyro.size == 0:
        gyro = np.zeros((0, 3), dtype=np.float32)
    elif gyro.ndim == 1:
        gyro = gyro.reshape(-1, 3)
    if accel.size == 0:
        accel = np.zeros((0, 3), dtype=np.float32)
    elif accel.ndim == 1:
        accel = accel.reshape(-1, 3)
    return gyro_ts, gyro, accel_ts, accel
