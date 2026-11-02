"""Pack/unpack per-frame quad-camera JPEG blobs (one write per frame)."""

from __future__ import annotations

import struct

from ego_capture_studio.capture.camera_map import ALL_LEROBOT_VIDEO_KEYS

FRAME_BIN_MAGIC = b"DLB1"
FRAME_BIN_VERSION = 1
_FRAME_HEADER = struct.Struct("<4sBB")  # magic, version, n_keys
_ENTRY_HEADER = struct.Struct("<H I")  # key_len, jpeg_len


def pack_frame_bin(camera_jpegs: dict[str, bytes]) -> bytes:
    """Serialize 4 camera JPEGs in stable ALL_LEROBOT_VIDEO_KEYS order."""
    chunks: list[bytes] = [_FRAME_HEADER.pack(FRAME_BIN_MAGIC, FRAME_BIN_VERSION, len(ALL_LEROBOT_VIDEO_KEYS))]
    for key in ALL_LEROBOT_VIDEO_KEYS:
        jpeg = camera_jpegs.get(key)
        if not jpeg:
            jpeg = b""
        key_b = key.encode("utf-8")
        if len(key_b) > 65535:
            raise ValueError(f"video key too long: {key}")
        chunks.append(_ENTRY_HEADER.pack(len(key_b), len(jpeg)))
        chunks.append(key_b)
        chunks.append(jpeg)
    return b"".join(chunks)


def unpack_frame_bin(data: bytes) -> dict[str, bytes]:
    """Parse frame blob; missing keys are omitted."""
    if len(data) < _FRAME_HEADER.size:
        raise ValueError("frame bin too short")
    magic, version, n_keys = _FRAME_HEADER.unpack_from(data, 0)
    if magic != FRAME_BIN_MAGIC:
        raise ValueError(f"bad frame bin magic: {magic!r}")
    if version != FRAME_BIN_VERSION:
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


def frame_bin_path(frames_dir, frame_index: int):
    from pathlib import Path

    return Path(frames_dir) / f"{frame_index:08d}.bin"
