"""ego_web: each capture start rotates sessionId in checkpoint."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("ego_web", _ROOT / "ego_web.py")
assert _SPEC and _SPEC.loader
ego_web = importlib.util.module_from_spec(_SPEC)
sys.modules["ego_web"] = ego_web
_SPEC.loader.exec_module(ego_web)


class CaptureSessionRotateTest(unittest.TestCase):
    def test_begin_new_capture_session_resets_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ck = Path(tmp) / "checkpoint.json"
            ego_web.CHECKPOINT_PATH = ck
            ck.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "sessionId": "sess_old",
                        "nextFrameIndex": 900,
                        "segmentSeq": 3,
                        "task": "task-a",
                    }
                ),
                encoding="utf-8",
            )
            (Path(tmp) / "strict_emit_ts.json").write_text('{"ts": 1}', encoding="utf-8")

            sid = ego_web._begin_new_capture_session()
            self.assertTrue(sid.startswith("sess_"))
            self.assertNotEqual(sid, "sess_old")

            data = json.loads(ck.read_text(encoding="utf-8"))
            self.assertEqual(data["sessionId"], sid)
            self.assertEqual(data["nextFrameIndex"], 0)
            self.assertEqual(data["segmentSeq"], 0)
            self.assertEqual(data["task"], "task-a")
            self.assertFalse((Path(tmp) / "strict_emit_ts.json").exists())


if __name__ == "__main__":
    unittest.main()
