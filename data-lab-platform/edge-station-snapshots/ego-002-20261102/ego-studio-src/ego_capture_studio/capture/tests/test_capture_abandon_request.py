"""capture_abandon_request.json contract."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import capture_abandon_request as car


class CaptureAbandonRequestTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._path = Path(self._tmpdir.name) / "capture_abandon_request.json"
        os.environ["EGO_CAPTURE_ABANDON_REQUEST"] = str(self._path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        os.environ.pop("EGO_CAPTURE_ABANDON_REQUEST", None)

    def test_write_and_match_session(self) -> None:
        car.write_abandon_request("sess_a")
        self.assertTrue(car.abandon_requested_for("sess_a"))
        self.assertFalse(car.abandon_requested_for("sess_b"))

    def test_clear(self) -> None:
        car.write_abandon_request("sess_x")
        car.clear_abandon_request()
        self.assertFalse(car.abandon_requested_for("sess_x"))


if __name__ == "__main__":
    unittest.main()
