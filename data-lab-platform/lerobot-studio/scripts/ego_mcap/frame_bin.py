"""Minimal DLB1 frame bin unpack for MCAP export."""

from __future__ import annotations

import struct

FRAME_BIN_MAGIC = b"DLB1"
_FRAME_HEADER = struct.Struct("<4sBB")
_ENTRY_HEADER = struct.Struct("<H I")

ALL_LEROBOT_VIDEO_KEYS: tuple[str, ...] = (
    "observation.images.camera_front_left",
    "observation.images.camera_front_right",
    "observation.images.camera_rear_left",
    "observation.images.camera_rear_right",
)


def unpack_frame_bin(data: bytes) -> dict[str, bytes]:
    if len(data) < _FRAME_HEADER.size:
        raise ValueError("frame bin too short")
    magic, version, n_keys = _FRAME_HEADER.unpack_from(data, 0)
    if magic != FRAME_BIN_MAGIC:
        raise ValueError(f"bad frame bin magic: {magic!r}")
    if version != 1:
        raise ValueError(f"unsupported frame bin version: {version}")
    offset = _FRAME_HEADER.size
    out: dict[str, bytes] = {}
    for _ in range(n_keys):
        if offset + _ENTRY_HEADER.size > len(data):
            raise ValueError("truncated frame bin entry header")
        key_len, jpeg_len = _ENTRY_HEADER.unpack_from(data, offset)
        offset += _ENTRY_HEADER.size
        end_key = offset + key_len
        end_jpeg = end_key + jpeg_len
        if end_jpeg > len(data):
            raise ValueError("truncated frame bin entry payload")
        key = data[offset:end_key].decode("utf-8")
        jpeg = data[end_key:end_jpeg]
        if jpeg:
            out[key] = jpeg
        offset = end_jpeg
    return out
