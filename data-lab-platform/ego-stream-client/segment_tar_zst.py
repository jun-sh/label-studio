"""Pack closed capture segments as tar.zst archives for single-file upload."""

from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover - optional until pip install
    zstd = None  # type: ignore[assignment]

ZSTD_LEVEL = 1
_SEGMENT_MEMBERS = ("manifest.json", "rows.jsonl", "imu_raw.jsonl")


def pack_segment_tar_zst(segment_dir: Path, out_path: Path) -> tuple[Path, str]:
    """
    Archive segment_dir (manifest.json, rows.jsonl, frames/*.bin) into tar.zst.
    Returns (out_path, sha256_hex of compressed blob).
    """
    segment_dir = Path(segment_dir).resolve()
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    compressed = _compress_tar_bytes(_build_tar_bytes(segment_dir))
    out_path.write_bytes(compressed)
    digest = hashlib.sha256(compressed).hexdigest()
    return out_path, digest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_tar_bytes(segment_dir: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name in _SEGMENT_MEMBERS:
            member = segment_dir / name
            if member.is_file():
                tar.add(member, arcname=name, recursive=False)
        frames = segment_dir / "frames"
        if frames.is_dir():
            for bin_path in sorted(frames.glob("*.bin")):
                tar.add(bin_path, arcname=f"frames/{bin_path.name}", recursive=False)
        streams = segment_dir / "streams"
        if streams.is_dir():
            for mp4_path in sorted(streams.glob("*.mp4")):
                tar.add(mp4_path, arcname=f"streams/{mp4_path.name}", recursive=False)
    return buf.getvalue()


def _compress_tar_bytes(raw: bytes) -> bytes:
    if zstd is not None:
        return zstd.ZstdCompressor(level=ZSTD_LEVEL).compress(raw)
    proc = subprocess.run(
        ["zstd", f"-{ZSTD_LEVEL}", "-c", "-T1"],
        input=raw,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "zstandard module missing and zstd CLI failed; "
            "pip install zstandard or install zstd package"
        )
    return proc.stdout


def segment_archive_basename(session_id: str, segment_id: str) -> str:
    """Unique ready/ filename: one session may reuse seg_000001 per capture cycle."""
    sid = str(session_id or "").strip()
    seg = str(segment_id or "").strip()
    if not seg.startswith("seg_"):
        raise ValueError(f"invalid segment_id: {segment_id!r}")
    if sid.startswith("sess_"):
        return f"{sid}__{seg}.tar.zst"
    return f"{seg}.tar.zst"


def parse_segment_archive_name(filename: str) -> tuple[str, str]:
    """Return (session_id, segment_id). session_id empty for legacy seg_*.tar.zst."""
    name = str(filename or "").strip()
    if name.endswith(".tar.zst"):
        name = name[: -len(".tar.zst")]
    if "__" in name and name.startswith("sess_"):
        sid, seg = name.split("__", 1)
        if not seg.startswith("seg_"):
            raise ValueError(f"not a segment archive name: {filename}")
        return sid, seg
    if name.startswith("seg_"):
        return "", name
    raise ValueError(f"not a segment archive name: {filename}")
