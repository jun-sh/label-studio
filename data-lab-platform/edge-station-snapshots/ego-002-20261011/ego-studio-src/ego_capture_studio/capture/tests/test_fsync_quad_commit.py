"""Unit tests for FSYNC-quad commit helpers."""

from __future__ import annotations

from collections import deque

import tests.conftest  # noqa: F401 — stubs

from ego_capture_studio.capture.fsync_quad_commit import (
    drop_primary_head,
    fsync_quad_take_frame,
    pick_fsync_quad,
)
from ego_capture_studio.capture.oak_4p_capture import Oak4pEgoRecorder, _CamRingSample


def test_fsync_quad_take_frame_aligned_quad() -> None:
    cam_list = ["CAM_A", "CAM_B", "CAM_C", "CAM_D"]
    rings = {
        "CAM_A": deque([_CamRingSample(1_000_000, b"a")]),
        "CAM_B": deque([_CamRingSample(1_000_400, b"b")]),
        "CAM_C": deque([_CamRingSample(999_700, b"c")]),
        "CAM_D": deque([_CamRingSample(1_000_100, b"d")]),
    }
    keys = {oak: f"video.{oak.lower()}" for oak in cam_list}
    out = fsync_quad_take_frame(
        rings,
        cam_list,
        primary_socket="CAM_A",
        align_max_ns=1_000_000,
        ring_sample_fn=Oak4pEgoRecorder._strict_ring_sample,
        socket_to_key=keys,
    )
    assert out is not None
    capture_out, offsets, primary_ts = out
    assert primary_ts == 1_000_000
    assert len(capture_out) == 4
    assert all(abs(v) <= 1_000_000 for v in offsets.values())
    assert all(len(rings[oak]) == 0 for oak in cam_list)


def test_fsync_quad_skips_stale_secondary_heads() -> None:
    cam_list = ["CAM_A", "CAM_B"]
    rings = {
        "CAM_A": deque([_CamRingSample(1_000_000, b"a")]),
        "CAM_B": deque(
            [
                _CamRingSample(966_000, b"stale"),
                _CamRingSample(1_000_200, b"match"),
            ]
        ),
    }
    keys = {oak: f"video.{oak.lower()}" for oak in cam_list}
    picked = pick_fsync_quad(
        rings,
        cam_list,
        primary_socket="CAM_A",
        align_max_ns=1_000_000,
        ring_sample_fn=Oak4pEgoRecorder._strict_ring_sample,
    )
    assert picked is not None
    _, primary_ts = picked
    assert primary_ts == 1_000_000
    out = fsync_quad_take_frame(
        rings,
        cam_list,
        primary_socket="CAM_A",
        align_max_ns=1_000_000,
        ring_sample_fn=Oak4pEgoRecorder._strict_ring_sample,
        socket_to_key=keys,
    )
    assert out is not None
    assert len(rings["CAM_B"]) == 0


def test_fsync_quad_rejects_outside_window() -> None:
    cam_list = ["CAM_A", "CAM_B"]
    rings = {
        "CAM_A": deque([_CamRingSample(1_000_000, b"a")]),
        "CAM_B": deque([_CamRingSample(1_003_000, b"b")]),
    }
    keys = {oak: f"video.{oak.lower()}" for oak in cam_list}
    out = fsync_quad_take_frame(
        rings,
        cam_list,
        primary_socket="CAM_A",
        align_max_ns=1_000_000,
        ring_sample_fn=Oak4pEgoRecorder._strict_ring_sample,
        socket_to_key=keys,
    )
    assert out is None


def test_drop_primary_head() -> None:
    rings = {"CAM_A": deque([_CamRingSample(1, b"a"), _CamRingSample(2, b"b")])}
    assert drop_primary_head(rings, primary_socket="CAM_A") is True
    assert len(rings["CAM_A"]) == 1
