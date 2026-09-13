"""Parallel-drain POC entry point — delegates to production H264 backend installer."""

from __future__ import annotations

import ego_capture_studio.cli.record_oak_stream as _record_main
from ego_capture_studio.capture.capture_frame_profile import FrameProfiler
from ego_capture_studio.capture.capture_h264_production import install_h264_production_backend


def main() -> None:
    install_h264_production_backend(_record_main)
    try:
        _record_main.main()
    finally:
        prof = FrameProfiler.get()
        if prof is not None:
            prof.report(prefix="capture_profile_final")


if __name__ == "__main__":
    main()
