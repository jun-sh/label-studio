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
        cached = {"beep_epoch": 99.5, "captured": 0, "capture_fps": 0.0}
        ego_web._journal_cache = (time.monotonic(), 42.0, cached)
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
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=False),
            mock.patch.object(ego_web, "_oak_warm_seconds_remaining", return_value=0),
            mock.patch.object(ego_web, "_read_capture_live_stats", return_value={}),
            mock.patch.object(ego_web, "_journal_capture_info", return_value={"beep_epoch": None}),
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
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=False),
            mock.patch.object(ego_web, "_oak_warm_seconds_remaining", return_value=0),
            mock.patch.object(
                ego_web,
                "_read_capture_live_stats",
                return_value={"beep_epoch": beep, "frame_count": 90},
            ),
            mock.patch.object(
                ego_web,
                "_journal_capture_info",
                return_value={"beep_epoch": beep, "captured": 90, "capture_fps": 30.0},
            ),
            mock.patch.object(ego_web, "_storage_free_bytes", return_value=10**12),
            mock.patch.object(ego_web, "_count_segments", return_value=0),
            mock.patch.object(ego_web, "_maybe_stop_idle_standby_preview"),
        ):
            status = ego_web._build_status()
        self.assertEqual(status["state"], "recording")
        self.assertTrue(status["frames_writing"])
        self.assertGreaterEqual(status["duration"], 2)
        self.assertLessEqual(status["duration"], 5)

    def test_build_status_ignores_stale_journal_beep_without_live_stats(self) -> None:
        ego_web._journal_cache = None
        stale_beep = time.time() - 120.0
        with (
            mock.patch.object(ego_web, "_reconcile_orphan_capture_stack"),
            mock.patch.object(ego_web, "_capture_active", return_value=True),
            mock.patch.object(ego_web, "_capture_unit_state", return_value="active"),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=False),
            mock.patch.object(ego_web, "_oak_warm_seconds_remaining", return_value=0),
            mock.patch.object(ego_web, "_read_capture_live_stats", return_value={"beep_epoch": None}),
            mock.patch.object(
                ego_web,
                "_journal_capture_info",
                return_value={"beep_epoch": stale_beep, "captured": 3600},
            ),
            mock.patch.object(ego_web, "_storage_free_bytes", return_value=10**12),
            mock.patch.object(ego_web, "_count_segments", return_value=0),
            mock.patch.object(ego_web, "_maybe_stop_idle_standby_preview"),
        ):
            status = ego_web._build_status()
        self.assertEqual(status["state"], "warming")
        self.assertEqual(status["duration"], 0)
        self.assertFalse(status["frames_writing"])

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

    def test_warm_start_allocates_new_session_before_resume(self) -> None:
        ego_web._last_action_mono = 0.0
        ego_web._last_completed_action = ""
        with (
            mock.patch.object(ego_web, "_wait_for_stop_release", return_value=True),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=True),
            mock.patch.object(
                ego_web, "_begin_new_capture_session", return_value="sess_warm_new"
            ) as new_sess,
            mock.patch.object(
                ego_web, "_start_capture_warm_resume", return_value=(True, "")
            ) as warm,
        ):
            ok, msg = ego_web._run_capture_action("start")
        self.assertTrue(ok)
        self.assertEqual(msg, "")
        new_sess.assert_called_once()
        warm.assert_called_once()

    def test_warm_stop_falls_back_when_unit_activating(self) -> None:
        with (
            mock.patch.object(ego_web, "_capture_unit_running", return_value=False),
            mock.patch.object(ego_web, "_capture_active", return_value=True),
            mock.patch.object(ego_web, "_stop_capture_hard", return_value=True) as hard,
            mock.patch.object(ego_web, "_systemctl") as ctl,
        ):
            ok = ego_web._stop_capture_to_warm()
        self.assertTrue(ok)
        hard.assert_called_once()
        ctl.assert_not_called()

    def test_warm_stop_falls_back_on_crash_loop(self) -> None:
        states = iter(["active", "activating", "activating"])
        with (
            mock.patch.object(ego_web, "_capture_unit_running", return_value=True),
            mock.patch.object(
                ego_web, "_capture_unit_state", side_effect=lambda u=None: next(states)
            ),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=False),
            mock.patch.object(ego_web, "_capture_fully_idle", return_value=False),
            mock.patch.object(ego_web, "_stop_capture_hard", return_value=True) as hard,
            mock.patch.object(
                ego_web,
                "_systemctl",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
        ):
            ok = ego_web._stop_capture_to_warm()
        self.assertTrue(ok)
        hard.assert_called_once()

    def test_warm_stop_falls_back_on_timeout(self) -> None:
        ego_web.WARM_STOP_WAIT_S = 0.05
        ego_web.STOP_TIMEOUT_S = 0.05
        with (
            mock.patch.object(ego_web, "_capture_unit_running", return_value=True),
            mock.patch.object(ego_web, "_capture_unit_state", return_value="active"),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=False),
            mock.patch.object(ego_web, "_capture_fully_idle", return_value=False),
            mock.patch.object(ego_web, "_stop_capture_hard", return_value=True) as hard,
            mock.patch.object(
                ego_web,
                "_systemctl",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
        ):
            ok = ego_web._stop_capture_to_warm()
        self.assertTrue(ok)
        hard.assert_called_once()

    def test_warm_start_falls_back_to_cold_when_resume_fails(self) -> None:
        ego_web._last_action_mono = 0.0
        ego_web._last_completed_action = ""
        with (
            mock.patch.object(ego_web, "_wait_for_stop_release", return_value=True),
            mock.patch.object(
                ego_web,
                "_oak_warm_idle_active",
                side_effect=[True, True, False, False],
            ),
            mock.patch.object(
                ego_web, "_begin_new_capture_session", return_value="sess_cold"
            ),
            mock.patch.object(
                ego_web, "_start_capture_warm_resume", return_value=(False, "resume failed")
            ),
            mock.patch.object(ego_web, "_capture_unit_running", return_value=False),
            mock.patch.object(ego_web, "_stop_capture_hard", return_value=True) as hard,
            mock.patch.object(ego_web, "_capture_active", return_value=False),
            mock.patch.object(ego_web, "_capture_stopping", return_value=False),
            mock.patch.object(ego_web, "_ensure_capture_fully_stopped", return_value=True),
            mock.patch.object(
                ego_web, "_ensure_preview_handoff_for_capture", return_value=(True, "")
            ),
            mock.patch.object(ego_web, "_grab_preview_bytes", return_value=b""),
            mock.patch.object(ego_web, "_stop_standby_preview"),
            mock.patch.object(ego_web, "_preview_port_in_use", return_value=False),
            mock.patch.object(
                ego_web,
                "_systemctl",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as ctl,
        ):
            ego_web._run_capture_action("start")
        hard.assert_called_once()
        ctl.assert_any_call("start", ego_web.CAPTURE_TARGET, timeout=ego_web.START_TIMEOUT_S)


if __name__ == "__main__":
    unittest.main()
