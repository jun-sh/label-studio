"""Unit tests for H264 FIFO ring consumption (GOP-safe subsample)."""

from __future__ import annotations

from collections import deque

from ego_capture_studio.capture.oak_4p_capture import Oak4pEgoRecorder, _CamRingSample


def _recorder() -> Oak4pEgoRecorder:
    rec = Oak4pEgoRecorder()
    rec._hw_h264 = True
    return rec


def test_consume_h264_ring_sample_fifo_in_order() -> None:
    rec = _recorder()
    ring: deque[_CamRingSample] = deque(
        [
            _CamRingSample(1_000_000_000, b"a"),
            _CamRingSample(1_033_333_333, b"b"),
            _CamRingSample(1_066_666_666, b"c"),
        ]
    )
    s1 = rec._consume_h264_ring_sample(ring, "CAM_A", 1_000_000_000)
    s2 = rec._consume_h264_ring_sample(ring, "CAM_A", 1_033_333_333)
    assert s1 is not None and s1.payload == b"a"
    assert s2 is not None and s2.payload == b"b"
    assert len(ring) == 1 and ring[0].payload == b"c"
    assert rec.h264_stale_drop_count() == 0


def test_consume_h264_drops_stale_packets() -> None:
    rec = _recorder()
    ring: deque[_CamRingSample] = deque(
        [
            _CamRingSample(0, b"old"),
            _CamRingSample(1_000_000_000, b"ok"),
        ],
        maxlen=8,
    )
    sample = rec._consume_h264_ring_sample(ring, "CAM_A", 1_000_000_000)
    assert sample is not None and sample.payload == b"ok"
    assert rec.h264_stale_drop_count() == 1
