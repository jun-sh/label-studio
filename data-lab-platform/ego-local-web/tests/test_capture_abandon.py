"""ego_web: abandon capture deletes session dirs and skips uploaded segments."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("ego_web", _ROOT / "ego_web.py")
assert _SPEC and _SPEC.loader
ego_web = importlib.util.module_from_spec(_SPEC)
sys.modules["ego_web"] = ego_web
_SPEC.loader.exec_module(ego_web)


class CaptureAbandonTest(unittest.TestCase):
    def test_delete_session_data_removes_shm_and_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shm = Path(tmp) / "shm" / "sessions" / "sess_test"
            disk = Path(tmp) / "disk" / "sessions" / "sess_test"
            (shm / "segments" / "seg_000001").mkdir(parents=True)
            (disk / "segments" / "seg_000001").mkdir(parents=True)
            (shm / "segments" / "seg_000001" / "manifest.json").write_text("{}", encoding="utf-8")

            ego_web.SEGMENT_ACTIVE_ROOT = Path(tmp) / "shm"
            ego_web.SEGMENT_ROOT = Path(tmp) / "disk"

            ego_web._delete_session_data("sess_test")
            self.assertFalse(shm.exists())
            self.assertFalse(disk.exists())

    def test_session_has_uploaded_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ego_web.SEGMENT_ROOT = Path(tmp)
            seg = Path(tmp) / "sessions" / "sess_a" / "segments" / "seg_000001"
            seg.mkdir(parents=True)
            (seg / "manifest.json").write_text(
                json.dumps({"uploaded": True}),
                encoding="utf-8",
            )
            self.assertTrue(ego_web._session_has_uploaded_segments("sess_a"))

            seg2 = Path(tmp) / "sessions" / "sess_b" / "segments" / "seg_000001"
            seg2.mkdir(parents=True)
            (seg2 / "manifest.json").write_text(
                json.dumps({"uploaded": False, "closed": True}),
                encoding="utf-8",
            )
            self.assertFalse(ego_web._session_has_uploaded_segments("sess_b"))

    def test_session_is_completely_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ego_web.SEGMENT_ROOT = Path(tmp)
            ego_web.SEGMENT_ACTIVE_ROOT = Path(tmp) / "shm"
            empty = Path(tmp) / "sessions" / "sess_empty"
            empty.mkdir(parents=True)
            self.assertTrue(ego_web._session_is_completely_empty("sess_empty"))

            meta = Path(tmp) / "sessions" / "sess_meta" / "meta"
            meta.mkdir(parents=True)
            (meta / "camera_intrinsics.json").write_text("{}", encoding="utf-8")
            self.assertFalse(ego_web._session_is_completely_empty("sess_meta"))

    def test_session_has_segment_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ego_web.SEGMENT_ROOT = Path(tmp)
            sid = "sess_seg"
            seg = Path(tmp) / "sessions" / sid / "segments" / "seg_000001"
            seg.mkdir(parents=True)
            self.assertTrue(ego_web._session_has_segment_dirs(sid))
            self.assertFalse(ego_web._session_has_segment_dirs("sess_none"))

    def test_run_capture_abandon_uses_warm_stop_when_enabled(self) -> None:
        with (
            mock.patch.object(ego_web, "_capture_active", return_value=True),
            mock.patch.object(ego_web, "_read_checkpoint", return_value={"sessionId": "sess_w"}),
            mock.patch.object(ego_web, "_session_has_uploaded_segments", return_value=False),
            mock.patch.object(ego_web, "_stop_capture_abandon_to_warm", return_value=True) as warm,
            mock.patch.object(ego_web, "_stop_capture_hard") as hard,
            mock.patch.object(ego_web, "_delete_session_data") as delete,
            mock.patch.object(ego_web, "_log_abandon"),
        ):
            ok, msg = ego_web._run_capture_abandon()
        self.assertTrue(ok)
        self.assertEqual(msg, "")
        warm.assert_called_once()
        hard.assert_not_called()
        delete.assert_called_once_with("sess_w")


if __name__ == "__main__":
    unittest.main()
