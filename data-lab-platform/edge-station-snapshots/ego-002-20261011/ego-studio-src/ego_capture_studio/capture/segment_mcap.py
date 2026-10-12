"""Pack closed MCAP segments for upload (.mcap.zst)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import zstandard as zstd

MCAP_ZST_SUFFIX = ".mcap.zst"


def pack_segment_mcap_zst(segment_mcap: Path, *, level: int = 1) -> Path:
    segment_mcap = Path(segment_mcap)
    if not segment_mcap.is_file():
        raise FileNotFoundError(segment_mcap)
    out = segment_mcap.with_suffix(segment_mcap.suffix + ".zst")
    cctx = zstd.ZstdCompressor(level=level)
    with open(segment_mcap, "rb") as src, open(out, "wb") as dst:
        cctx.copy_stream(src, dst)
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_mcap_archive_name(name: str) -> tuple[str, str] | None:
    """Parse sess_*__seg_*.mcap.zst or .mcap basename."""
    base = name
    if base.endswith(".zst"):
        base = base[: -len(".zst")]
    if not base.endswith(".mcap"):
        return None
    stem = base[: -len(".mcap")]
    if "__" not in stem:
        return None
    session_id, segment_id = stem.split("__", 1)
    if not session_id.startswith("sess_") or not segment_id.startswith("seg_"):
        return None
    return session_id, segment_id
