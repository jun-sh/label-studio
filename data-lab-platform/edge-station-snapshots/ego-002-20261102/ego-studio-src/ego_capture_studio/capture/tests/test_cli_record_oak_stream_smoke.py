"""Smoke test: CLI entry must not drift from capture implementation."""
from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


class CliRecordOakStreamSmokeTest(unittest.TestCase):
    def test_cli_wrapper_calls_capture_main(self) -> None:
        text = (_ROOT / "cli" / "record_oak_stream.py").read_text(encoding="utf-8")
        self.assertIn("record_oak_stream import main", text)
        self.assertNotIn('run_name="__main__"', text)

    def test_capture_module_has_sys_for_h264_backend(self) -> None:
        text = (_ROOT / "record_oak_stream.py").read_text(encoding="utf-8")
        self.assertIn("import sys", text)


if __name__ == "__main__":
    unittest.main()
