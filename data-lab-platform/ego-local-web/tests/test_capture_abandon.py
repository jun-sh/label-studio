"""ego_web: abandon capture deletes session dirs and skips uploaded segments."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
