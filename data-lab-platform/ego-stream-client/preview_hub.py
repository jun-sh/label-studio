"""Thread-safe latest-frame hub for MJPEG preview (non-blocking for capture/upload)."""

from __future__ import annotations

import threading
from typing import Dict, Optional

import numpy as np

# Short URL segment -> LeRobot video feature key
PREVIEW_CAMERAS: tuple[tuple[str, str], ...] = (
    ("head_left", "observation.images.camera_head_left"),
    ("head_right", "observation.images.camera_head_right"),
    ("depth", "observation.images.camera_depth_head"),
    ("cam_02", "observation.images.camera_02"),
)

VALID_PREVIEW_CAMS = frozenset(short for short, _ in PREVIEW_CAMERAS)


class PreviewHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Optional[Dict[str, np.ndarray]] = None
        self._pending_jpegs: Optional[Dict[str, bytes]] = None
        self._jpeg: Dict[str, bytes] = {}

    def offer(self, camera_frames: dict[str, np.ndarray]) -> None:
        """Non-blocking: keep only the latest synced quad-frame (legacy RGB path)."""
        snap: Dict[str, np.ndarray] = {}
        for short, key in PREVIEW_CAMERAS:
            rgb = camera_frames.get(key)
            if rgb is not None:
                snap[short] = rgb
        if not snap:
            return
        with self._lock:
            self._pending = snap

    def offer_jpegs(self, camera_jpegs: dict[str, bytes]) -> None:
        """Non-blocking: latest per-camera JPEG bytes (shared with upload/ring)."""
        snap: Dict[str, bytes] = {}
        for short, key in PREVIEW_CAMERAS:
            data = camera_jpegs.get(key)
            if data:
                snap[short] = data
        if not snap:
            return
        with self._lock:
            self._pending_jpegs = snap

    def take_pending(self) -> Optional[Dict[str, np.ndarray]]:
        with self._lock:
            pending = self._pending
            self._pending = None
            return pending

    def take_pending_jpegs(self) -> Optional[Dict[str, bytes]]:
        with self._lock:
            pending = self._pending_jpegs
            self._pending_jpegs = None
            return pending

    def set_jpeg(self, cam: str, data: bytes) -> None:
        with self._lock:
            self._jpeg[cam] = data

    def get_jpeg(self, cam: str) -> Optional[bytes]:
        with self._lock:
            return self._jpeg.get(cam)
