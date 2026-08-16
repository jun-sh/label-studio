"""Mux in-memory H.264 NAL chunks into per-camera MP4 at segment close (single IO burst)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SEGMENT_H264_FPS = int(os.environ.get("OAK_DEVICE_FPS", "30"))
SEGMENT_H264_MIN_MP4 = max(1, int(os.environ.get("SEGMENT_H264_MIN_MP4", "4")))
SEGMENT_H264_STRICT = os.environ.get("SEGMENT_H264_STRICT", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)


def _segment_h264_enabled() -> bool:
    return os.environ.get("SEGMENT_H264", "0").strip().lower() in ("1", "true", "yes")


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


def preflight_segment_h264_capture() -> str:
    """Startup check when SEGMENT_H264=1 (requires ffmpeg on capture host)."""
    if not _segment_h264_enabled():
        return ""
    path = resolve_ffmpeg()
    print(f"segment_h264_preflight ok ffmpeg={path}", flush=True)
    return path


def verify_segment_stream_mp4s(
    segment_dir: Path,
    *,
    min_mp4: int | None = None,
) -> list[Path]:
    """Raise if segment H264 outputs are missing or undersized."""
    if not _segment_h264_enabled():
        return []
    streams_dir = Path(segment_dir) / "streams"
    mp4s = sorted(streams_dir.glob("*.mp4")) if streams_dir.is_dir() else []
    need = SEGMENT_H264_MIN_MP4 if min_mp4 is None else max(1, int(min_mp4))
    if len(mp4s) < need:
        raise RuntimeError(
            f"segment_stream_mp4_insufficient segment={Path(segment_dir).name} "
            f"got={len(mp4s)} need>={need}"
        )
    for mp4_path in mp4s:
        if mp4_path.stat().st_size < 1024:
            raise RuntimeError(f"segment_stream_mp4_empty path={mp4_path}")
    return mp4s


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
    verify_segment_stream_mp4s(segment_dir, min_mp4=len(mp4_paths) if mp4_paths else SEGMENT_H264_MIN_MP4)
    return mp4_paths
