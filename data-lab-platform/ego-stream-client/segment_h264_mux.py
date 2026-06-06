"""Mux in-memory H.264 NAL chunks into per-camera MP4 at segment close (single IO burst)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SEGMENT_H264_FPS = int(os.environ.get("OAK_DEVICE_FPS", "30"))


def mux_h264_buffers_to_mp4(
    segment_dir: Path,
    streams: dict[str, list[bytes]],
    *,
    fps: int | None = None,
    delete_raw: bool = True,
) -> list[Path]:
    """Write concatenated Annex-B once per camera, then ffmpeg copy-mux to MP4."""
    if not streams:
        return []
    out_dir = segment_dir / "streams"
    out_dir.mkdir(parents=True, exist_ok=True)
    rate = fps if fps is not None else SEGMENT_H264_FPS
    mp4_paths: list[Path] = []
    for key, chunks in streams.items():
        if not chunks:
            continue
        safe = key.replace(".", "_")
        h264_path = out_dir / f"{safe}.h264"
        mp4_path = out_dir / f"{safe}.mp4"
        h264_path.write_bytes(b"".join(chunks))
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "h264",
                "-r",
                str(rate),
                "-i",
                str(h264_path),
                "-c",
                "copy",
                str(mp4_path),
            ],
            check=True,
            timeout=180,
        )
        if delete_raw:
            try:
                h264_path.unlink()
            except OSError:
                pass
        mp4_paths.append(mp4_path)
    return mp4_paths
