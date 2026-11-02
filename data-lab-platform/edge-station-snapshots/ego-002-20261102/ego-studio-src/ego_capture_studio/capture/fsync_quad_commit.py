"""FSYNC quad commit: primary CAM_A tick + timestamp-aligned 4-way pick (POC)."""

from __future__ import annotations

import os
from collections import deque
from typing import Any, Callable

from ego_capture_studio.capture.oak_4p_capture import _CamRingSample

EGO_FSYNC_QUAD_ALIGN_MAX_NS = int(os.environ.get("EGO_FSYNC_QUAD_ALIGN_MAX_NS", "1000000"))
EGO_FSYNC_QUAD_WAIT_MS = max(0.0, float(os.environ.get("EGO_FSYNC_QUAD_WAIT_MS", "25.0")))


def consume_ring_through_sample(
    ring: deque[_CamRingSample],
    sample: _CamRingSample,
) -> bool:
    """Drop ring heads until sample is consumed (skipped H.264 AUs not written to MCAP)."""
    target_ts = int(sample.ts_ns)
    while ring:
        head = ring[0]
        if head is sample or int(head.ts_ns) == target_ts:
            ring.popleft()
            return True
        ring.popleft()
    return False


def pick_fsync_quad(
    cam_rings: dict[str, deque[_CamRingSample]],
    cam_list: list[str],
    *,
    primary_socket: str,
    align_max_ns: int,
    ring_sample_fn: Callable[..., _CamRingSample | None],
) -> tuple[dict[str, _CamRingSample], int] | None:
    """Return aligned quad for primary ring head ts, or None if any cam misses the window."""
    primary_ring = cam_rings.get(primary_socket)
    if not primary_ring:
        return None
    primary_ts_ns = int(primary_ring[0].ts_ns)
    picked: dict[str, _CamRingSample] = {}
    for oak in cam_list:
        ring = cam_rings.get(oak)
        if not ring:
            return None
        sample = ring_sample_fn(ring, primary_ts_ns, align_max_ns)
        if sample is None:
            return None
        picked[oak] = sample
    return picked, primary_ts_ns


def fsync_quad_missing_socket(
    cam_rings: dict[str, deque[_CamRingSample]],
    cam_list: list[str],
    *,
    primary_socket: str,
    align_max_ns: int,
    ring_sample_fn: Callable[..., _CamRingSample | None],
) -> str | None:
    """First camera socket that cannot satisfy the FSYNC alignment window."""
    primary_ring = cam_rings.get(primary_socket)
    if not primary_ring:
        return primary_socket
    primary_ts_ns = int(primary_ring[0].ts_ns)
    for oak in cam_list:
        ring = cam_rings.get(oak)
        if not ring:
            return oak
        if ring_sample_fn(ring, primary_ts_ns, align_max_ns) is None:
            return oak
    return None


def fsync_quad_take_frame(
    cam_rings: dict[str, deque[_CamRingSample]],
    cam_list: list[str],
    *,
    primary_socket: str,
    align_max_ns: int,
    ring_sample_fn: Callable[..., _CamRingSample | None],
    socket_to_key: dict[str, str],
) -> tuple[dict[str, Any], dict[str, int], int] | None:
    """Pick aligned quad at primary head ts and consume rings through each sample."""
    picked_pair = pick_fsync_quad(
        cam_rings,
        cam_list,
        primary_socket=primary_socket,
        align_max_ns=align_max_ns,
        ring_sample_fn=ring_sample_fn,
    )
    if picked_pair is None:
        return None
    picked, primary_ts_ns = picked_pair
    offsets: dict[str, int] = {}
    capture_out: dict[str, Any] = {}
    for oak in cam_list:
        sample = picked[oak]
        if not consume_ring_through_sample(cam_rings[oak], sample):
            return None
        lerobot_key = socket_to_key[oak]
        capture_out[lerobot_key] = sample.payload
        offsets[lerobot_key] = int(sample.ts_ns) - int(primary_ts_ns)
    if len(capture_out) < len(cam_list):
        return None
    return capture_out, offsets, primary_ts_ns


def drop_primary_head(
    cam_rings: dict[str, deque[_CamRingSample]],
    *,
    primary_socket: str,
) -> bool:
    primary_ring = cam_rings.get(primary_socket)
    if not primary_ring:
        return False
    primary_ring.popleft()
    return True
