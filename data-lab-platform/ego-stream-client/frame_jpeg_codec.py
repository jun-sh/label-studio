"""Shared JPEG encoding for capture / upload / ring / preview (encode once per camera per frame)."""

from __future__ import annotations

import logging
import os

import numpy as np

try:
    import cv2
except ImportError as e:  # pragma: no cover
    raise RuntimeError("opencv-python-headless is required for frame_jpeg_codec") from e

JPEG_QUALITY = int(os.environ.get("DATALAB_JPEG_QUALITY", "70"))
OPENCV_NUM_THREADS = int(os.environ.get("OPENCV_NUM_THREADS", "2"))
CAPTURE_JPEG_MAX_EDGE = int(os.environ.get("CAPTURE_JPEG_MAX_EDGE", "0"))
_CONFIGURED = False
_TURBOJPEG = None
_TURBOJPEG_TRIED = False

_logger = logging.getLogger("datalab.jpeg")


def jpeg_encoder_name() -> str:
    """Return active encoder backend ('turbojpeg' or 'opencv')."""
    return "turbojpeg" if _get_turbojpeg() is not None else "opencv"


def log_jpeg_encoder_info() -> str:
    """Print encoder backend once at process start (visible in journal)."""
    name = jpeg_encoder_name()
    msg = f"jpeg_encoder={name} quality={JPEG_QUALITY}"
    print(msg, flush=True)
    _logger.info(msg)
    return name


def configure_opencv_threads() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    try:
        cv2.setNumThreads(max(0, OPENCV_NUM_THREADS))
    except Exception:
        pass
    _CONFIGURED = True


def _get_turbojpeg():
    global _TURBOJPEG, _TURBOJPEG_TRIED
    if _TURBOJPEG_TRIED:
        return _TURBOJPEG
    _TURBOJPEG_TRIED = True
    try:
        from turbojpeg import TJPF_BGR, TJSAMP_420, TurboJPEG

        _TURBOJPEG = (TurboJPEG(), TJPF_BGR, TJSAMP_420)
        _logger.info("jpeg_encoder=turbojpeg quality=%s", JPEG_QUALITY)
    except Exception as exc:
        _logger.info("jpeg_encoder=opencv turbojpeg_unavailable=%s", str(exc)[:120])
        _TURBOJPEG = None
    return _TURBOJPEG


def _resize_rgb_max_edge(rgb: np.ndarray, max_edge: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    longest = max(h, w)
    if longest <= max_edge:
        return rgb
    scale = max_edge / float(longest)
    new_w = max(2, int(w * scale))
    new_h = max(2, int(h * scale))
    return cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)


def encode_rgb_to_jpeg(rgb: np.ndarray) -> bytes:
    """Encode one RGB frame to JPEG (TurboJPEG preferred, OpenCV fallback)."""
    configure_opencv_threads()
    if CAPTURE_JPEG_MAX_EDGE > 0:
        rgb = _resize_rgb_max_edge(rgb, CAPTURE_JPEG_MAX_EDGE)
    turbo = _get_turbojpeg()
    if turbo is not None:
        jpeg_enc, tjpf_bgr, subsample = turbo
        bgr = np.ascontiguousarray(rgb[:, :, ::-1], dtype=np.uint8)
        return jpeg_enc.encode(
            bgr,
            quality=JPEG_QUALITY,
            pixel_format=tjpf_bgr,
            jpeg_subsample=subsample,
        )
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return buf.tobytes()


def encode_camera_jpegs(camera_frames: dict[str, np.ndarray]) -> dict[str, bytes]:
    """One JPEG per LeRobot camera key; skip missing/None frames."""
    out: dict[str, bytes] = {}
    for key, rgb in camera_frames.items():
        if rgb is None:
            continue
        out[key] = encode_rgb_to_jpeg(rgb)
    return out
