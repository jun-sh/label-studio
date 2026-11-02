"""Warm USB + Cold Episode env helpers."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import oak_warm_session  # noqa: E402


class WarmColdEpisodeEnvTest(unittest.TestCase):
    def test_default_on_when_warm_idle(self) -> None:
        with mock.patch.dict(os.environ, {"EGO_OAK_WARM_IDLE": "1"}, clear=False):
            os.environ.pop("EGO_OAK_WARM_COLD_EPISODE", None)
            self.assertTrue(oak_warm_session.oak_warm_cold_episode_enabled())

    def test_off_when_warm_idle_off(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"EGO_OAK_WARM_IDLE": "0", "EGO_OAK_WARM_COLD_EPISODE": "1"},
            clear=False,
        ):
            self.assertFalse(oak_warm_session.oak_warm_cold_episode_enabled())

    def test_can_be_disabled(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"EGO_OAK_WARM_IDLE": "1", "EGO_OAK_WARM_COLD_EPISODE": "0"},
            clear=False,
        ):
            self.assertFalse(oak_warm_session.oak_warm_cold_episode_enabled())


if __name__ == "__main__":
    unittest.main()
