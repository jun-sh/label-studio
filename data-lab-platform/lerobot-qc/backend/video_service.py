"""Video streaming helpers (read-only source access)."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any

from dataset_manager import DatasetState

TRIMMED_CACHE: Path | None = None


def configure_cache(cache_root: Path) -> None:
    global TRIMMED_CACHE
    TRIMMED_CACHE = cache_root / "trimmed_videos"
    TRIMMED_CACHE.mkdir(parents=True, exist_ok=True)


def trim_video_with_ffmpeg(input_path: Path, output_path: Path, start_time: float, end_time: float) -> bool:
    duration = end_time - start_time
    if duration <= 0:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_time),
        "-i",
        str(input_path),
        "-t",
        str(duration),
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            cmd_reencode = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start_time),
                "-i",
                str(input_path),
                "-t",
                str(duration),
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-c:a",
                "aac",
                str(output_path),
            ]
            result = subprocess.run(cmd_reencode, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                return False
        return output_path.is_file()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_video_duration(video_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return 0.0


def parse_range(range_header: str, file_size: int) -> tuple[int, int] | None:
    match = re.match(r"bytes=(\d+)-(\d*)", range_header)
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else file_size - 1
    if start >= file_size:
        return None
    end = min(end, file_size - 1)
    return start, end


def trimmed_cache_path(video_path: Path, episode_index: int, start_time: float, end_time: float) -> Path:
    assert TRIMMED_CACHE is not None
    key = f"{video_path}_{episode_index}_{start_time:.3f}_{end_time:.3f}"
    digest = hashlib.md5(key.encode()).hexdigest()[:16]
    return TRIMMED_CACHE / f"ep{episode_index}_{digest}.mp4"


def resolve_stream_path(state: DatasetState, episode_index: int, video_key: str | None = None) -> Path:
    video_key = video_key or state.video_key
    original = state.video_path(episode_index, video_key=video_key)
    offsets = state.video_offsets(video_key or "", episode_index)
    start_time = offsets["video_start_time"]
    end_time = offsets["video_end_time"]
    needs_trim = start_time > 0.1 or (
        end_time > 0 and end_time < get_video_duration(original) - 0.5
    )
    if needs_trim and end_time > start_time:
        cache_path = trimmed_cache_path(original, episode_index, start_time, end_time)
        if not cache_path.is_file():
            ok = trim_video_with_ffmpeg(original, cache_path, start_time, end_time)
            if not ok:
                return original
        if cache_path.is_file():
            return cache_path
    return original


def iter_file_range(path: Path, start: int, length: int) -> Any:
    with open(path, "rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            chunk = fh.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
