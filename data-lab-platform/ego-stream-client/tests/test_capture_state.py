"""Tests for capture_state resolution."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from capture_state import remote_preview_allowed, resolve_capture_state


class CaptureStateTests(unittest.TestCase):
    def test_idle_when_standby_preview_active(self) -> None:
        with patch("capture_state._systemctl_is_active") as active:
            active.side_effect = lambda unit: unit == "ecs-preview-standby.service"
            self.assertEqual(resolve_capture_state(), "idle")
            self.assertTrue(remote_preview_allowed("idle"))

    def test_recording_when_capture_stack_active(self) -> None:
        with patch("capture_state._systemctl_is_active") as active:
            active.side_effect = lambda unit: unit == "ecs-oak-capture-stack.target"
            self.assertEqual(resolve_capture_state(), "recording")
            self.assertFalse(remote_preview_allowed("recording"))

    def test_offline_when_nothing_active(self) -> None:
        with patch("capture_state._systemctl_is_active", return_value=False):
            self.assertEqual(resolve_capture_state(), "offline")


if __name__ == "__main__":
    unittest.main()
