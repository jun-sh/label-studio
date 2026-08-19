"""Test harness: ego-stream-client on PYTHONPATH + minimal ego_capture_studio stubs."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_ego_capture_studio_stubs() -> None:
    if "ego_capture_studio.capture.ego_spec" in sys.modules:
        return

    pkg = types.ModuleType("ego_capture_studio")
    capture = types.ModuleType("ego_capture_studio.capture")

    ego_spec = types.ModuleType("ego_capture_studio.capture.ego_spec")
    ego_spec.OBS_STATE_DIM = 6
    ego_spec.OBS_POSE_DIM = 7
    ego_spec.OBS_HANDS_DIM = 14

    lerobot_episode = types.ModuleType("ego_capture_studio.capture.lerobot_episode")
    lerobot_episode.identity_pose_xyzw = lambda: np.zeros(7, dtype=float)

    camera_intrinsics = types.ModuleType("ego_capture_studio.capture.camera_intrinsics")
    camera_intrinsics.INTRINSICS_REL_PATH = "camera_intrinsics.json"
    camera_intrinsics.write_camera_intrinsics_json = lambda *args, **kwargs: Path("/tmp/intrinsics.json")

    intrinsics_store = types.ModuleType("ego_capture_studio.capture.intrinsics_store")
    intrinsics_store.write_session_intrinsics = lambda *args, **kwargs: Path("/tmp/intrinsics.json")

    frame_bin_codec = types.ModuleType("ego_capture_studio.capture.frame_bin_codec")
    frame_bin_codec.pack_frame_bin = lambda camera_jpegs: b"DLB1" + b"\x00" * max(1, len(camera_jpegs))
    frame_bin_codec.unpack_frame_bin = lambda data: {}

    camera_map = types.ModuleType("ego_capture_studio.capture.camera_map")
    camera_map.ALL_LEROBOT_VIDEO_KEYS = (
        "observation.images.camera_front_left",
        "observation.images.camera_front_right",
        "observation.images.camera_rear_left",
        "observation.images.camera_rear_right",
    )

    segment_tar_zst = types.ModuleType("ego_capture_studio.capture.segment_tar_zst")
    segment_tar_zst.pack_segment_tar_zst = lambda *args, **kwargs: None
    segment_tar_zst.parse_segment_archive_name = lambda name: ("sess_x", "seg_x")
    segment_tar_zst.sha256_file = lambda path: "0" * 64

    upload_status = types.ModuleType("ego_capture_studio.capture.upload_status")
    upload_status.live_ui_enabled = lambda: False

    class _NoopStatusWriter:
        @classmethod
        def get_default(cls) -> "_NoopStatusWriter":
            return cls()

        def set_uploading(self, **kwargs: object) -> None:
            return None

        def record_ok(self, **kwargs: object) -> str:
            return "ok"

        def record_fail(self, **kwargs: object) -> str:
            return "fail"

        def record_quarantine_skip(self, **kwargs: object) -> None:
            return None

        def reset_session(self, session_id: str) -> None:
            return None

        def refresh_queue(self, **kwargs: object) -> None:
            return None

        @property
        def path(self) -> Path:
            return Path("/tmp/ego-upload-status.json")

    upload_status.UploadStatusWriter = _NoopStatusWriter

    pkg.__path__ = []  # type: ignore[attr-defined]
    capture.__package__ = "ego_capture_studio.capture"
    capture.__path__ = []  # type: ignore[attr-defined]

    sys.modules["ego_capture_studio"] = pkg
    sys.modules["ego_capture_studio.capture"] = capture
    sys.modules["ego_capture_studio.capture.ego_spec"] = ego_spec
    sys.modules["ego_capture_studio.capture.lerobot_episode"] = lerobot_episode
    sys.modules["ego_capture_studio.capture.camera_intrinsics"] = camera_intrinsics
    sys.modules["ego_capture_studio.capture.intrinsics_store"] = intrinsics_store
    sys.modules["ego_capture_studio.capture.frame_bin_codec"] = frame_bin_codec
    sys.modules["ego_capture_studio.capture.camera_map"] = camera_map
    sys.modules["ego_capture_studio.capture.segment_tar_zst"] = segment_tar_zst
    sys.modules["ego_capture_studio.capture.upload_status"] = upload_status

    import importlib

    segment_store = importlib.import_module("segment_store")
    sys.modules["ego_capture_studio.capture.segment_store"] = segment_store


_install_ego_capture_studio_stubs()
