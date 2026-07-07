"""Session-qualified export archive names (multi-episode per day)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from segment_tar_zst import parse_segment_archive_name, segment_archive_basename


class SegmentArchiveNamingTest(unittest.TestCase):
    def test_session_qualified_basename(self) -> None:
        name = segment_archive_basename(
            "sess_aaa",
            "seg_000001",
        )
        self.assertEqual(name, "sess_aaa__seg_000001.tar.zst")
        sid, seg = parse_segment_archive_name(name)
        self.assertEqual(sid, "sess_aaa")
        self.assertEqual(seg, "seg_000001")

    def test_legacy_basename(self) -> None:
        sid, seg = parse_segment_archive_name("seg_000001.tar.zst")
        self.assertEqual(sid, "")
        self.assertEqual(seg, "seg_000001")


if __name__ == "__main__":
    unittest.main()
