"""Mux in-memory H.264 NAL chunks into per-camera MP4 at segment close (single IO burst)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SEGMENT_H264_FPS = int(os.environ.get("OAK_DEVICE_FPS", "30"))


def resolve_ffmpeg() -> str:
    """Return ffmpeg binary path (130 capture host must have ffmpeg in PATH)."""
    explicit = os.environ.get("FFMPEG", "").strip()
    if explicit:
        return explicit
    found = shutil.which("ffmpeg")
    if not found:
        raise FileNotFoundError(
            "ffmpeg not found on capture host; install ffmpeg or set FFMPEG=/path/to/ffmpeg"
        )
    return found


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
    ffmpeg = resolve_ffmpeg()
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
        try:
            subprocess.run(
                [
                    ffmpeg,
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
        except (subprocess.CalledProcessError, OSError) as exc:
            print(
                f"segment_h264_mux FAIL camera={key} segment={segment_dir.name} "
                f"h264_bytes={h264_path.stat().st_size} err={exc}",
                flush=True,
            )
            raise
        if delete_raw:
            try:
                h264_path.unlink()
            except OSError:
                pass
        mp4_paths.append(mp4_path)
    return mp4_paths
