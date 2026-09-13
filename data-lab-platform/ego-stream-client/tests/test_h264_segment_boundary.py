"""Tests for h264_segment_boundary (P1a)."""

from __future__ import annotations

import pytest

from h264_segment_boundary import (
    camera_counts_parity_ok,
    classify_nal_types,
    contains_idr,
    contains_sps_pps,
    iter_nal_units,
    require_camera_parity,
    slice_annex_b_from_first_idr,
)


def _fake_nal(ntype: int, payload: bytes = b"\xab\xcd") -> bytes:
    return b"\x00\x00\x00\x01" + bytes([(ntype & 0x1F)]) + payload


def test_iter_nal_units_splits_annex_b() -> None:
    data = _fake_nal(7) + _fake_nal(8) + _fake_nal(5)
    nals = list(iter_nal_units(data))
    assert len(nals) == 3


def test_classify_nal_types_idr_sps_pps() -> None:
    data = _fake_nal(7) + _fake_nal(8) + _fake_nal(5)
    types = classify_nal_types(data)
    assert types == {5, 7, 8}
    assert contains_sps_pps(data)
    assert contains_idr(data)


def test_contains_idr_avcc_length_prefixed() -> None:
    idr = bytes([0x00, 0x00, 0x00, 0x02, 0x65, 0x88])
    assert contains_idr(idr)


def test_camera_counts_parity_ok() -> None:
    ok, _ = camera_counts_parity_ok(
        {"front_left": 10, "front_right": 10, "rear_left": 10, "rear_right": 10},
    )
    assert ok
    bad, msg = camera_counts_parity_ok(
        {"front_left": 10, "front_right": 10, "rear_left": 9, "rear_right": 10},
    )
    assert not bad
    assert "mismatch" in msg


def test_slice_annex_b_from_first_idr_drops_leading_p_slice() -> None:
    pframe = _fake_nal(1)
    cluster = _fake_nal(7) + _fake_nal(8) + _fake_nal(5) + _fake_nal(1)
    data = pframe + cluster
    assert slice_annex_b_from_first_idr(data) == cluster


def test_require_camera_parity_raises() -> None:
    with pytest.raises(RuntimeError, match="mismatch"):
        require_camera_parity(
            {"front_left": 2, "front_right": 2, "rear_left": 1, "rear_right": 2},
        )
