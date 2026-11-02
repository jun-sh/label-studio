"""capture_stop_state.json contract."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import capture_stop_state as css


class CaptureStopStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._path = Path(self._tmpdir.name) / "capture_stop_state.json"
        os.environ["EGO_CAPTURE_STOP_STATE"] = str(self._path)

    def tearDown(self) -> None:
        os.environ.pop("EGO_CAPTURE_STOP_STATE", None)
        self._tmpdir.cleanup()

    def test_phase_progression_and_clear(self) -> None:
        css.write_stop_state(css.PHASE_RECORDING, "sess_a")
        self.assertEqual(css.stop_phase(), css.PHASE_RECORDING)
        css.write_stop_state(css.PHASE_STOPPING, "sess_a")
        css.write_stop_state(css.PHASE_FLUSHING, "sess_a")
        css.write_stop_state(css.PHASE_SEALED, "sess_a")
        self.assertTrue(css.stop_data_safe_on_disk())
        self.assertTrue(css.stop_is_complete())
        css.write_stop_state(css.PHASE_WARM_IDLE, "sess_a")
        self.assertTrue(css.stop_is_complete())
        css.clear_stop_state()
        self.assertEqual(css.stop_phase(), "")

    def test_atomic_write(self) -> None:
        css.write_stop_state(css.PHASE_SEALING, "sess_b", detail="closing writer")
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self.assertEqual(raw["phase"], css.PHASE_SEALING)
        self.assertEqual(raw["session_id"], "sess_b")
        self.assertEqual(raw["detail"], "closing writer")


if __name__ == "__main__":
    unittest.main()
