"""ego_web: formal capture timer anchored to capture_ready_beep journal line."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("ego_web", _ROOT / "ego_web.py")
assert _SPEC and _SPEC.loader
ego_web = importlib.util.module_from_spec(_SPEC)
sys.modules["ego_web"] = ego_web
_SPEC.loader.exec_module(ego_web)


class CaptureTimerTest(unittest.TestCase):
    def test_journal_beep_epoch_parses_short_unix(self) -> None:
        ego_web._journal_cache = None
        since = 1_700_000_000.0
        proc = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="1788516801.836862 host python: capture_ready_beep=played backend=aplay\n",
            stderr="",
        )
        with mock.patch.object(ego_web.subprocess, "run", return_value=proc) as run:
            ts = ego_web._journal_beep_epoch(since)
        self.assertAlmostEqual(ts or 0.0, 1788516801.836862)
        run.assert_called_once()

    def test_journal_beep_epoch_uses_cache(self) -> None:
        ego_web._journal_cache = (time.monotonic(), 42.0, 99.5)
        with mock.patch.object(ego_web.subprocess, "run") as run:
            ts = ego_web._journal_beep_epoch(42.0)
        self.assertEqual(ts, 99.5)
        run.assert_not_called()

    def test_build_status_warming_before_beep(self) -> None:
        ego_web._journal_cache = None
        with (
            mock.patch.object(ego_web, "_reconcile_orphan_capture_stack"),
            mock.patch.object(ego_web, "_capture_active", return_value=True),
            mock.patch.object(ego_web, "_capture_unit_state", return_value="active"),
            mock.patch.object(ego_web, "_journal_beep_epoch", return_value=None),
            mock.patch.object(ego_web, "_storage_free_bytes", return_value=10**12),
            mock.patch.object(ego_web, "_count_segments", return_value=0),
            mock.patch.object(ego_web, "_maybe_stop_idle_standby_preview"),
        ):
            status = ego_web._build_status()
        self.assertEqual(status["state"], "warming")
        self.assertEqual(status["duration"], 0)
        self.assertFalse(status["frames_writing"])

    def test_build_status_recording_after_beep(self) -> None:
        ego_web._journal_cache = None
        beep = time.time() - 3.0
        with (
            mock.patch.object(ego_web, "_reconcile_orphan_capture_stack"),
            mock.patch.object(ego_web, "_capture_active", return_value=True),
            mock.patch.object(ego_web, "_capture_unit_state", return_value="active"),
            mock.patch.object(ego_web, "_journal_beep_epoch", return_value=beep),
            mock.patch.object(ego_web, "_storage_free_bytes", return_value=10**12),
            mock.patch.object(ego_web, "_count_segments", return_value=0),
            mock.patch.object(ego_web, "_maybe_stop_idle_standby_preview"),
        ):
            status = ego_web._build_status()
        self.assertEqual(status["state"], "recording")
        self.assertTrue(status["frames_writing"])
        self.assertGreaterEqual(status["duration"], 2)
        self.assertLessEqual(status["duration"], 5)

    def test_reconcile_orphan_capture_stack(self) -> None:
        def _state(unit: str) -> str:
            if unit == ego_web.CAPTURE_RECORD_UNIT:
                return "inactive"
            if unit == ego_web.CAPTURE_TARGET:
                return "active"
            return "inactive"

        with (
            mock.patch.object(ego_web, "_capture_unit_state", side_effect=_state),
            mock.patch.object(ego_web, "_systemctl") as stop,
        ):
            ego_web._reconcile_orphan_capture_stack()
        stop.assert_called_once_with("stop", ego_web.CAPTURE_TARGET, timeout=15)


if __name__ == "__main__":
    unittest.main()
