"""Video streaming helpers (read-only source access)."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from dataset_manager import DatasetState

logger = logging.getLogger(__name__)

TRIMMED_CACHE: Path | None = None

DELIVERY_DURATION_TOLERANCE_SEC = 0.001


def configure_cache(cache_root: Path) -> None:
    global TRIMMED_CACHE
    TRIMMED_CACHE = cache_root / "trimmed_videos"
    TRIMMED_CACHE.mkdir(parents=True, exist_ok=True)


def expected_video_duration(frame_count: int, fps: float) -> float:
    if frame_count <= 0 or not fps:
        return 0.0
    return frame_count / fps


def duration_within_tolerance(
    actual: float,
    expected: float,
    fps: float,
    *,
    strict: bool = True,
) -> bool:
    if expected <= 0:
        return actual <= 0
    if strict:
        tolerance = min(DELIVERY_DURATION_TOLERANCE_SEC, 0.1 / fps)
    else:
        tolerance = min(0.25, 0.5 / fps)
    return abs(actual - expected) <= tolerance


def _delivery_tmp_path(output_path: Path) -> Path:
    """Temp path with a valid video extension for ffmpeg (not `.mp4.tmp`)."""
    return output_path.with_name(f"{output_path.stem}.tmp{output_path.suffix}")


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
            return trim_video_reencode(input_path, output_path, start_time, end_time)
        if output_path.is_file():
            actual = get_video_duration(output_path)
            if actual > duration + 0.25:
                output_path.unlink(missing_ok=True)
                return trim_video_reencode(input_path, output_path, start_time, end_time)
        return output_path.is_file()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def trim_video_reencode(input_path: Path, output_path: Path, start_time: float, end_time: float) -> bool:
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
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return result.returncode == 0 and output_path.is_file()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def trim_video_reencode_frames(
    input_path: Path,
    output_path: Path,
    start_time: float,
    frame_count: int,
    fps: float,
) -> bool:
    if frame_count <= 0 or not fps:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _delivery_tmp_path(output_path)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(max(0.0, start_time)),
        "-i",
        str(input_path),
        "-frames:v",
        str(frame_count),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(tmp),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.warning(
                "Frame-exact ffmpeg trim failed for %s: %s",
                input_path,
                (result.stderr or "")[-400:],
            )
            tmp.unlink(missing_ok=True)
            return False
        if not validate_delivery_video(tmp, frame_count, fps):
            tmp.unlink(missing_ok=True)
            expected = expected_video_duration(frame_count, fps)
            return trim_video_reencode(input_path, output_path, start_time, start_time + expected)
        tmp.replace(output_path)
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        tmp.unlink(missing_ok=True)
        return False


def validate_delivery_video(path: Path, frame_count: int, fps: float) -> bool:
    if not path.is_file() or frame_count <= 0 or not fps:
        return False
    expected = expected_video_duration(frame_count, fps)
    actual = get_video_duration(path)
    if not duration_within_tolerance(actual, expected, fps, strict=True):
        return False
    nb_frames = get_video_frame_count(path)
    if nb_frames is not None and nb_frames != frame_count:
        return False
    return True


def trim_video_for_delivery(
    input_path: Path,
    output_path: Path,
    start_time: float,
    end_time: float,
    frame_count: int,
    fps: float,
) -> bool:
    """Trim/copy source video so file duration matches parquet frame count."""
    if frame_count <= 0 or not fps:
        return False

    needs_trim = start_time > 0.05 or end_time > 0
    if needs_trim:
        if not trim_video_with_ffmpeg(input_path, output_path, start_time, end_time):
            return trim_video_reencode_frames(input_path, output_path, start_time, frame_count, fps)
    else:
        shutil.copy2(input_path, output_path)

    if validate_delivery_video(output_path, frame_count, fps):
        return True

    logger.info(
        "Re-encoding %s for frame-exact delivery (%d frames @ %.3f fps)",
        output_path.name,
        frame_count,
        fps,
    )
    return trim_video_reencode_frames(input_path, output_path, start_time, frame_count, fps)


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


def get_video_frame_count(video_path: Path) -> int | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_frames",
        "-show_entries",
        "stream=nb_read_frames",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return None


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
        expected = end_time - start_time
        if cache_path.is_file():
            actual = get_video_duration(cache_path)
            if actual > expected + 0.35:
                cache_path.unlink(missing_ok=True)
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
