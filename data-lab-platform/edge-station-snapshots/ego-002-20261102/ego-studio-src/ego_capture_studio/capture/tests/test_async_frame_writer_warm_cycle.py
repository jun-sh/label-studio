"""AsyncFrameWriter must survive warm stop → warm resume cycles."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from async_frame_writer import AsyncFrameWriter


class AsyncFrameWriterWarmCycleTest(unittest.TestCase):
    def test_restarts_consumer_after_flush_for_next_episode(self) -> None:
        append_calls: list[int] = []

        def append_fn(_writer, _hub, _rec, **kwargs) -> None:
            append_calls.append(int(kwargs["timestamp_ns"]))

        writer = AsyncFrameWriter(append_fn, maxsize=8)
        writer.start()
        writer.enqueue(None, None, None, timestamp_ns=1, capture_out={}, preview_out={}, imu6=None)
        writer.enqueue(None, None, None, timestamp_ns=2, capture_out={}, preview_out={}, imu6=None)
        time.sleep(0.2)
        writer.flush(timeout_s=2.0)
        self.assertEqual(append_calls, [1, 2])

        writer.enqueue(None, None, None, timestamp_ns=3, capture_out={}, preview_out={}, imu6=None)
        time.sleep(0.2)
        writer.flush(timeout_s=2.0)
        self.assertEqual(append_calls, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
