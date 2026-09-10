"""H.264 segment-boundary helpers (P1a): NAL inspection and four-camera parity."""

from __future__ import annotations

from typing import Iterable

NAL_TYPE_IDR = 5
NAL_TYPE_SPS = 7
NAL_TYPE_PPS = 8

CAMERA_ROLE_KEYS = ("front_left", "front_right", "rear_left", "rear_right")


def iter_nal_units(data: bytes) -> Iterable[bytes]:
    """Split Annex-B H.264 bitstream into NAL units (includes start codes)."""
    if not data:
        return
    buf = bytes(data)
    if b"\x00\x00\x01" not in buf and b"\x00\x00\x00\x01" not in buf:
        yield from _iter_avcc_nal_units(buf)
        return
    i = 0
    n = len(buf)
    starts: list[int] = []
    while i < n - 3:
        if buf[i : i + 3] == b"\x00\x00\x01":
            starts.append(i)
            i += 3
            continue
        if i < n - 4 and buf[i : i + 4] == b"\x00\x00\x00\x01":
            starts.append(i)
            i += 4
            continue
        i += 1
    if not starts:
        yield buf
        return
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else n
        yield buf[start:end]


def _nal_type(nal: bytes) -> int | None:
    if len(nal) < 4:
        return None
    if nal.startswith(b"\x00\x00\x01"):
        header = nal[3]
    elif nal.startswith(b"\x00\x00\x00\x01"):
        header = nal[4]
    else:
        return None
    return int(header) & 0x1F


def classify_nal_types(data: bytes) -> set[int]:
    out: set[int] = set()
    for nal in iter_nal_units(data):
        ntype = _nal_type(nal)
        if ntype is not None:
            out.add(ntype)
    return out


def contains_idr(data: bytes) -> bool:
    return NAL_TYPE_IDR in classify_nal_types(data)


def contains_sps_pps(data: bytes) -> bool:
    types = classify_nal_types(data)
    return NAL_TYPE_SPS in types and NAL_TYPE_PPS in types


def leading_parameter_sets(data: bytes) -> bytes:
    """Return concatenated leading SPS/PPS/IDR NALs (for segment open validation)."""
    parts: list[bytes] = []
    for nal in iter_nal_units(data):
        ntype = _nal_type(nal)
        if ntype in (NAL_TYPE_SPS, NAL_TYPE_PPS, NAL_TYPE_IDR):
            parts.append(nal)
        elif parts:
            break
    return b"".join(parts)


def _iter_avcc_nal_units(data: bytes) -> Iterable[bytes]:
    """Parse length-prefixed (AVCC) NAL units from OAK VideoEncoder packets."""
    i = 0
    n = len(data)
    while i + 4 <= n:
        nal_len = int.from_bytes(data[i : i + 4], "big")
        i += 4
        if nal_len <= 0 or i + nal_len > n:
            break
        yield b"\x00\x00\x00\x01" + data[i : i + nal_len]
        i += nal_len


def camera_counts_parity_ok(counts: dict[str, int]) -> tuple[bool, str]:
    """True when all four camera roles share the same positive frame count."""
    vals = [int(counts.get(k) or 0) for k in CAMERA_ROLE_KEYS]
    if any(v <= 0 for v in vals):
        return False, f"camera_count_non_positive:{dict(zip(CAMERA_ROLE_KEYS, vals))}"
    if len(set(vals)) != 1:
        return False, f"camera_frame_mismatch:min={min(vals)},max={max(vals)}"
    return True, ""


def require_camera_parity(counts: dict[str, int]) -> None:
    ok, msg = camera_counts_parity_ok(counts)
    if not ok:
        raise RuntimeError(msg)
