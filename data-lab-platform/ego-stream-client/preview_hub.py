"""Thread-safe latest-frame hub for MJPEG preview (non-blocking for capture/upload)."""

from __future__ import annotations

import threading
from typing import Dict, Optional

import numpy as np

# Short URL segment -> LeRobot video feature key
PREVIEW_CAMERAS: tuple[tuple[str, str], ...] = (
    ("front_left", "observation.images.camera_front_left"),
    ("front_right", "observation.images.camera_front_right"),
    ("rear_left", "observation.images.camera_rear_left"),
    ("rear_right", "observation.images.camera_rear_right"),
)

# Historical preview URLs (read-only compat; map to canonical short names).
LEGACY_PREVIEW_CAM_ALIASES: dict[str, str] = {
    "head_left": "front_left",
    "head_right": "front_right",
    "depth": "rear_left",
    "cam_02": "rear_right",
}


def resolve_preview_cam(cam: str) -> str:
    return LEGACY_PREVIEW_CAM_ALIASES.get(cam, cam)


VALID_PREVIEW_CAMS = frozenset(short for short, _ in PREVIEW_CAMERAS) | frozenset(
    LEGACY_PREVIEW_CAM_ALIASES.keys()
)


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

    def set_jpeg(self, short: str, jpeg: bytes) -> None:
        canonical = resolve_preview_cam(short)
        with self._lock:
            self._jpeg[canonical] = jpeg

    def get_jpeg(self, short: str) -> Optional[bytes]:
        canonical = resolve_preview_cam(short)
        with self._lock:
            return self._jpeg.get(canonical)
