"""Preview port handoff: 8081 standby → 8080 capture start."""
from __future__ import annotations

import importlib.util
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


class PreviewHandoffTest(unittest.TestCase):
    def test_ensure_preview_handoff_retries_until_free(self) -> None:
        ports = iter([True, True, False])

        def port_busy() -> bool:
            return next(ports, False)

        with (
            mock.patch.object(ego_web, "_stop_standby_preview_impl", return_value=False),
            mock.patch.object(ego_web, "_preview_port_in_use", side_effect=port_busy),
            mock.patch.object(ego_web, "START_PREVIEW_HANDOFF_TIMEOUT_S", 5.0),
            mock.patch.object(ego_web, "START_PREVIEW_HANDOFF_POLL_S", 0.01),
        ):
            ok, msg = ego_web._ensure_preview_handoff_for_capture()
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_ensure_preview_handoff_fails_after_timeout(self) -> None:
        with (
            mock.patch.object(ego_web, "_stop_standby_preview_impl", return_value=False),
            mock.patch.object(ego_web, "_preview_port_in_use", return_value=True),
            mock.patch.object(ego_web, "START_PREVIEW_HANDOFF_TIMEOUT_S", 0.2),
            mock.patch.object(ego_web, "START_PREVIEW_HANDOFF_POLL_S", 0.05),
        ):
            ok, msg = ego_web._ensure_preview_handoff_for_capture()
        self.assertFalse(ok)
        self.assertIn("8765", msg)

    def test_failed_start_does_not_block_rapid_retry(self) -> None:
        ego_web._busy = False
        ego_web._busy_action = None
        ego_web._last_action_mono = time.monotonic()
        ego_web._last_completed_action = None

        with (
            mock.patch.object(ego_web, "_wait_for_stop_release", return_value=True),
            mock.patch.object(
                ego_web,
                "_ensure_preview_handoff_for_capture",
                return_value=(False, "预览端口 8765 未释放，请稍后再试"),
            ),
            mock.patch.object(ego_web, "_start_standby_preview"),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=False),
            mock.patch.object(ego_web, "_capture_active", return_value=False),
            mock.patch.object(ego_web, "_capture_stopping", return_value=False),
            mock.patch.object(ego_web, "_ensure_capture_fully_stopped", return_value=True),
            mock.patch.object(ego_web, "_grab_preview_bytes", return_value=None),
            mock.patch.object(ego_web, "_begin_new_capture_session", return_value="sess_test"),
            mock.patch.object(ego_web, "_fail_start_capture", side_effect=lambda sid, msg: (False, msg)),
        ):
            ok1, _ = ego_web._run_capture_action("start")
            ok2, msg2 = ego_web._run_capture_action("start")
        self.assertFalse(ok1)
        self.assertFalse(ok2)
        self.assertIn("8765", msg2)

    def test_stop_standby_skips_port_kill_when_capture_starting(self) -> None:
        with (
            mock.patch.object(ego_web, "_capture_unit_starting_or_active", return_value=True),
            mock.patch.object(ego_web, "_preview_port_in_use", return_value=True),
            mock.patch.object(ego_web.subprocess, "run") as run,
        ):
            ok = ego_web._stop_standby_preview(allow_port_kill=True)
        self.assertTrue(ok)
        run.assert_not_called()

    def test_release_standby_skips_during_capture(self) -> None:
        with (
            mock.patch.object(ego_web, "_capture_active", return_value=True),
            mock.patch.object(ego_web, "_capture_stopping", return_value=False),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=False),
        ):
            ok, msg = ego_web._release_standby_preview_if_safe()
        self.assertFalse(ok)
        self.assertEqual(msg, "capture_active")

    def test_release_keeps_standby_warm_when_idle(self) -> None:
        with (
            mock.patch.object(ego_web, "_capture_active", return_value=False),
            mock.patch.object(ego_web, "_capture_stopping", return_value=False),
            mock.patch.object(ego_web, "_capture_unit_starting_or_active", return_value=False),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=False),
            mock.patch.object(ego_web, "_touch_standby_preview_activity") as touch,
            mock.patch.object(ego_web, "_wake_standby_preview_if_idle") as wake,
            mock.patch.object(ego_web, "_stop_standby_preview") as stop,
        ):
            ok, msg = ego_web._release_standby_preview_if_safe()
        self.assertTrue(ok)
        self.assertEqual(msg, "standby_kept")
        touch.assert_called_once()
        wake.assert_called_once()
        stop.assert_not_called()

    def test_prepare_quad_schedules_warm_idle_end(self) -> None:
        ego_web._quad_warm_end_in_progress = False
        ego_web._quad_warm_end_requested = False
        with (
            mock.patch.object(ego_web, "PREVIEW_END_WARM_IDLE", True),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=True),
            mock.patch.object(ego_web, "_preview_capture_state", return_value="idle"),
            mock.patch.object(ego_web, "_wake_standby_preview_if_idle"),
            mock.patch.object(ego_web.threading, "Thread") as thread_cls,
        ):
            payload = ego_web.prepare_multi_preview_access()
        self.assertEqual(payload["phase"], "ending_warm_idle")
        thread_cls.assert_called_once()

    def test_status_get_does_not_schedule_warm_end(self) -> None:
        ego_web._quad_warm_end_in_progress = False
        ego_web._quad_warm_end_requested = False
        with (
            mock.patch.object(ego_web, "WEB_UI_MODE", "quad"),
            mock.patch.object(ego_web, "PREVIEW_END_WARM_IDLE", True),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=True),
            mock.patch.object(ego_web, "_capture_active", return_value=True),
            mock.patch.object(ego_web, "_capture_unit_state", return_value="active"),
            mock.patch.object(ego_web, "_capture_stop_phase", return_value="warm_idle"),
            mock.patch.object(ego_web, "_oak_warm_seconds_remaining", return_value=120),
            mock.patch.object(ego_web, "_read_capture_live_stats", return_value={}),
            mock.patch.object(ego_web, "_storage_free_bytes", return_value=10**12),
            mock.patch.object(ego_web, "_count_segments", return_value=0),
            mock.patch.object(ego_web.threading, "Thread") as thread_cls,
        ):
            status = ego_web._build_status()
            payload = ego_web._build_preview_cameras_payload()
        thread_cls.assert_not_called()
        self.assertEqual(status["state"], "idle")
        self.assertTrue(status["oak_warm_active"])
        self.assertEqual(status["quad_transition"], "warm_idle")
        self.assertFalse(payload["quad_allowed"])

    def test_build_status_quad_end_not_warming(self) -> None:
        ego_web._quad_warm_end_in_progress = True
        ego_web._quad_warm_end_requested = True
        with (
            mock.patch.object(ego_web, "_capture_active", return_value=True),
            mock.patch.object(ego_web, "_capture_unit_state", return_value="active"),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=False),
            mock.patch.object(ego_web, "_capture_stop_phase", return_value=""),
            mock.patch.object(ego_web, "_oak_warm_seconds_remaining", return_value=0),
            mock.patch.object(ego_web, "_read_capture_live_stats", return_value={}),
            mock.patch.object(ego_web, "_storage_free_bytes", return_value=10**12),
            mock.patch.object(ego_web, "_count_segments", return_value=0),
        ):
            status = ego_web._build_status()
        self.assertEqual(status["state"], "stopping")
        self.assertNotEqual(status["state"], "warming")
        ego_web._quad_warm_end_in_progress = False
        ego_web._quad_warm_end_requested = False

    def test_prepare_multi_preview_returns_phase(self) -> None:
        ego_web._quad_warm_end_in_progress = False
        ego_web._quad_warm_end_requested = False
        with (
            mock.patch.object(ego_web, "PREVIEW_END_WARM_IDLE", True),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=True),
            mock.patch.object(ego_web, "_preview_capture_state", return_value="idle"),
            mock.patch.object(ego_web, "_wake_standby_preview_if_idle"),
            mock.patch.object(ego_web.threading, "Thread") as thread_cls,
        ):
            payload = ego_web.prepare_multi_preview_access()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["phase"], "ending_warm_idle")
        thread_cls.assert_called_once()

    def test_view_switch_schedules_warm_idle_end(self) -> None:
        ego_web._quad_warm_end_in_progress = False
        ego_web._quad_warm_end_requested = False
        with (
            mock.patch.object(ego_web, "PREVIEW_END_WARM_IDLE", True),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=True),
            mock.patch.object(ego_web, "_preview_capture_state", return_value="idle"),
            mock.patch.object(ego_web, "_wake_standby_preview_if_idle"),
            mock.patch.object(ego_web.threading, "Thread") as thread_cls,
        ):
            phase = ego_web._prepare_view_switch_preview_access()
        self.assertEqual(phase, "ending_warm_idle")
        thread_cls.assert_called_once()

    def test_preview_cameras_marks_quad_blocked_during_recording(self) -> None:
        with (
            mock.patch.object(ego_web, "_preview_stream_live", return_value=True),
            mock.patch.object(ego_web, "_preview_capture_state", return_value="recording"),
            mock.patch.object(ego_web, "_oak_warm_idle_active", return_value=False),
            mock.patch.object(
                ego_web, "_preview_live_camera_ids", return_value=frozenset({"front_left"})
            ),
        ):
            payload = ego_web._build_preview_cameras_payload()
        self.assertFalse(payload["quad_allowed"])
        self.assertEqual(payload["capture_state"], "recording")
        self.assertTrue(payload["cameras"][0]["live"])
        self.assertFalse(payload["cameras"][1]["live"])


if __name__ == "__main__":
    unittest.main()
