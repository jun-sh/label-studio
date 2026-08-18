"""Idle-only OAK preview probe: MJPEG on :8765, no disk writes, no upload."""

from __future__ import annotations

import os
import signal
import sys
import time

try:
    from ego_capture_studio.capture.capture_state import resolve_capture_state
    from ego_capture_studio.capture.frame_jpeg_codec import (
        configure_opencv_threads,
        encode_camera_bgr_jpegs,
        log_jpeg_encoder_info,
    )
    from ego_capture_studio.capture.oak_4p_capture import Oak4pEgoRecorder
    from ego_capture_studio.capture.preview_server import start_preview_stack
except ImportError:
    from capture_state import resolve_capture_state  # type: ignore[no-redef]
    from frame_jpeg_codec import (  # type: ignore[no-redef]
        configure_opencv_threads,
        encode_camera_bgr_jpegs,
        log_jpeg_encoder_info,
    )
    from oak_4p_capture import Oak4pEgoRecorder  # type: ignore[no-redef]
    from preview_server import start_preview_stack  # type: ignore[no-redef]

_STANDBY_FPS = float(os.environ.get("STANDBY_PREVIEW_FPS", os.environ.get("PREVIEW_FPS", "8")))
_STANDBY_DEVICE_FPS = int(os.environ.get("STANDBY_DEVICE_FPS", os.environ.get("OAK_DEVICE_FPS", "15")))
_CHUNK_S = float(os.environ.get("STANDBY_PREVIEW_CHUNK_S", "120"))
_STOP = False


def _handle_stop(signum: int, _frame) -> None:
    global _STOP
    _STOP = True
    print(f"preview_standby signal={signum} shutting down", flush=True)


def _offer_preview(preview_hub, capture_out: dict, preview_out: dict, recorder: Oak4pEgoRecorder) -> None:
    if recorder.use_hw_jpeg:
        preview_hub.offer_jpegs(capture_out)
        if preview_out:
            preview_hub.offer_jpegs(preview_out)
    elif recorder.use_hw_h264:
        if preview_out:
            preview_hub.offer_jpegs(preview_out)
    else:
        preview_hub.offer_jpegs(encode_camera_bgr_jpegs(capture_out))
        if preview_out:
            preview_hub.offer_jpegs(encode_camera_bgr_jpegs(preview_out))


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    configure_opencv_threads()
    if log_jpeg_encoder_info() != "turbojpeg":
        print("WARNING: PyTurboJPEG unavailable; standby preview uses OpenCV", flush=True)

    fps = max(1.0, min(_STANDBY_FPS, 15.0))
    interval_s = 1.0 / fps
    preview_hub = start_preview_stack()
    recorder = Oak4pEgoRecorder(
        fps=max(1, int(round(fps))),
        device_fps=_STANDBY_DEVICE_FPS,
        enable_imu=False,
    )
    print(
        f"preview_standby start fps={fps} device_fps={_STANDBY_DEVICE_FPS} "
        f"capture_state={resolve_capture_state()}",
        flush=True,
    )
    recorder.connect()
    try:
        while not _STOP:
            if resolve_capture_state() != "idle":
                print("preview_standby exit capture_stack_active", flush=True)
                break
            chunk_end = time.monotonic() + _CHUNK_S
            while not _STOP and time.monotonic() < chunk_end:
                loop_started = time.monotonic()
                for _ts_ns, capture_out, preview_out, _imu6 in recorder.iter_synced_frames(0.25):
                    if _STOP:
                        break
                    if capture_out:
                        _offer_preview(preview_hub, capture_out, preview_out, recorder)
                    elapsed = time.monotonic() - loop_started
                    time.sleep(max(0.0, interval_s - elapsed))
                    break
    finally:
        recorder.stop()
        print("preview_standby stopped", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"preview_standby fatal: {exc}", flush=True)
        sys.exit(1)
