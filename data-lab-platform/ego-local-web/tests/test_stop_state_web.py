"""ego_web reads capture_stop_state.json for stop UX."""
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


class StopStateWebTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        ego_web.CHECKPOINT_PATH = root / "checkpoint.json"
        ego_web.CAPTURE_STOP_STATE_PATH = root / "capture_stop_state.json"
        ego_web.OAK_WARM_STATE_PATH = root / "oak_warm_state.json"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_stop_phase_message_from_json(self) -> None:
        ego_web.CAPTURE_STOP_STATE_PATH.write_text(
            json.dumps({"phase": "flushing", "session_id": "sess_x"}) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(
            ego_web._capture_stop_phase_message("flushing"),
            "正在写入帧缓冲，请勿断电…",
        )

    def test_build_status_shows_stop_phase_while_busy(self) -> None:
        ego_web.CAPTURE_STOP_STATE_PATH.write_text(
            json.dumps({"phase": "sealed", "session_id": "sess_x"}) + "\n",
            encoding="utf-8",
        )
        with (
            mock.patch.object(ego_web, "_reconcile_orphan_capture_stack"),
            mock.patch.object(ego_web, "_capture_active", return_value=True),
            mock.patch.object(ego_web, "_capture_unit_state", return_value="active"),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=False),
            mock.patch.object(ego_web, "_oak_warm_seconds_remaining", return_value=0),
            mock.patch.object(ego_web, "_read_capture_live_stats", return_value={}),
            mock.patch.object(ego_web, "_journal_capture_info", return_value={}),
            mock.patch.object(ego_web, "_storage_free_bytes", return_value=10**12),
            mock.patch.object(ego_web, "_count_segments", return_value=0),
            mock.patch.object(ego_web, "_maybe_stop_idle_standby_preview"),
        ):
            ego_web._busy = True
            ego_web._busy_action = "stop"
            status = ego_web._build_status()
        self.assertEqual(status["state"], "stopping")
        self.assertEqual(status["capture_stop_phase"], "sealed")
        self.assertIn("已保存", status["msg"])


if __name__ == "__main__":
    unittest.main()
