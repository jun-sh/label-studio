"""LeRobot-aligned selective video copy/re-encode for QC delivery rebuilds."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Literal

VideoProgressAction = Literal["copy", "reencode", "skip"]
VideoProgressCallback = Callable[[int, int, VideoProgressAction, str, int, int], None]

import pandas as pd

logger = logging.getLogger(__name__)

_CODEC_MAP = {
    "h264": "libx264",
    "av1": "libsvtav1",
    "ffv1": "ffv1",
    "libx264": "libx264",
    "libsvtav1": "libsvtav1",
}


def video_rel_path(info: dict[str, Any], video_key: str, chunk_index: int, file_index: int) -> str:
    features = info.get("features") or {}
    feature_info = (features.get(video_key) or {}).get("info") or {}
    rel_tpl = (
        feature_info.get("depth.video_path")
        or info.get("video_path")
        or "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
    )
    return rel_tpl.format(
        video_key=video_key,
        chunk_index=chunk_index,
        file_index=file_index,
    )


def video_encode_params(info: dict[str, Any], video_key: str) -> tuple[str, str]:
    feature_info = (info.get("features") or {}).get(video_key, {}).get("info") or {}
    raw_codec = str(feature_info.get("video.codec") or "h264")
    vcodec = _CODEC_MAP.get(raw_codec, raw_codec)
    pix_fmt = str(feature_info.get("video.pix_fmt") or "yuv420p")
    return vcodec, pix_fmt


def episodes_in_video_file(
    episodes_df: pd.DataFrame,
    video_key: str,
    chunk_index: int,
    file_index: int,
) -> list[int]:
    chunk_col = f"videos/{video_key}/chunk_index"
    file_col = f"videos/{video_key}/file_index"
    mask = (episodes_df[chunk_col] == chunk_index) & (episodes_df[file_col] == file_index)
    return [int(x) for x in episodes_df.loc[mask, "episode_index"].tolist()]


def iter_video_files(episodes_df: pd.DataFrame, video_keys: list[str]) -> list[tuple[str, int, int]]:
    files: set[tuple[str, int, int]] = set()
    for video_key in video_keys:
        chunk_col = f"videos/{video_key}/chunk_index"
        file_col = f"videos/{video_key}/file_index"
        if chunk_col not in episodes_df.columns or file_col not in episodes_df.columns:
            continue
        for _, row in episodes_df.iterrows():
            files.add((video_key, int(row[chunk_col]), int(row[file_col])))
    return sorted(files)


def probe_video_bitrate(input_path: Path) -> int | None:
    """Return source video bitrate in bits/s when available."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=bit_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(input_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        if value.isdigit():
            bitrate = int(value)
            return bitrate if bitrate > 0 else None
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.debug("Unable to probe bitrate for %s: %s", input_path, exc)
    return None


def _ffmpeg_video_encode_args(vcodec: str, pix_fmt: str, source_bitrate: int | None) -> list[str]:
    args = ["-pix_fmt", pix_fmt]
    if vcodec == "libx264":
        if source_bitrate:
            maxrate = max(int(source_bitrate * 1.1), source_bitrate + 50_000)
            bufsize = max(source_bitrate * 2, maxrate * 2)
            args.extend(
                [
                    "-b:v",
                    str(source_bitrate),
                    "-maxrate",
                    str(maxrate),
                    "-bufsize",
                    str(bufsize),
                ]
            )
        else:
            args.extend(["-crf", "23"])
        args.extend(["-preset", "medium"])
    return args


def keep_episodes_from_video(
    input_path: Path,
    output_path: Path,
    episodes_to_keep: list[tuple[int, int]],
    fps: float,
    vcodec: str,
    pix_fmt: str,
) -> None:
    """Keep frame ranges from a source video, matching LeRobot delete_episodes semantics."""
    if not episodes_to_keep:
        raise ValueError("No episode frame ranges to keep")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_bitrate = probe_video_bitrate(input_path)

    if vcodec == "libx264":
        _keep_episodes_from_video_ffmpeg(
            input_path,
            output_path,
            episodes_to_keep,
            fps,
            vcodec,
            pix_fmt,
            source_bitrate=source_bitrate,
        )
        return

    try:
        from lerobot.datasets.dataset_tools import _keep_episodes_from_video_with_av

        tmp_path = output_path.with_name(f"{output_path.stem}.tmp{output_path.suffix}")
        _keep_episodes_from_video_with_av(
            input_path,
            tmp_path,
            episodes_to_keep,
            fps,
            vcodec,
            pix_fmt,
        )
        tmp_path.replace(output_path)
        return
    except Exception as exc:
        logger.warning("PyAV selective re-encode failed for %s: %s", input_path.name, exc)

    _keep_episodes_from_video_ffmpeg(
        input_path,
        output_path,
        episodes_to_keep,
        fps,
        vcodec,
        pix_fmt,
        source_bitrate=source_bitrate,
    )


def _keep_episodes_from_video_ffmpeg(
    input_path: Path,
    output_path: Path,
    episodes_to_keep: list[tuple[int, int]],
    fps: float,
    vcodec: str,
    pix_fmt: str,
    *,
    source_bitrate: int | None = None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="qc-video-") as tmp_dir:
        tmp_root = Path(tmp_dir)
        encode_args = _ffmpeg_video_encode_args(vcodec, pix_fmt, source_bitrate)
        segment_paths: list[Path] = []
        for index, (start_frame, end_frame) in enumerate(episodes_to_keep):
            if end_frame <= start_frame:
                raise ValueError(f"Invalid frame range: {(start_frame, end_frame)}")
            segment_path = tmp_root / f"seg_{index:04d}{input_path.suffix}"
            start_time = start_frame / fps
            duration = (end_frame - start_frame) / fps
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
                vcodec,
                *encode_args,
                "-an",
                str(segment_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0 or not segment_path.is_file():
                raise RuntimeError(
                    f"ffmpeg segment extraction failed for {input_path}: {(result.stderr or '')[-400:]}"
                )
            segment_paths.append(segment_path)

        if len(segment_paths) == 1:
            shutil.copy2(segment_paths[0], output_path)
            if output_path.suffix.lower() == ".mp4":
                _ffmpeg_faststart(output_path)
            return

        concat_list = tmp_root / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{path.resolve()}'" for path in segment_paths) + "\n",
            encoding="utf-8",
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not output_path.is_file():
            raise RuntimeError(
                f"ffmpeg concat failed for {input_path}: {(result.stderr or '')[-400:]}"
            )
        if output_path.suffix.lower() == ".mp4":
            _ffmpeg_faststart(output_path)


def _ffmpeg_faststart(output_path: Path) -> None:
    tmp_path = output_path.with_name(f"{output_path.stem}.faststart{output_path.suffix}")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(output_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(tmp_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    if result.returncode == 0 and tmp_path.is_file():
        tmp_path.replace(output_path)


def format_video_progress(
    action: VideoProgressAction,
    video_key: str,
    chunk_index: int,
    file_index: int,
) -> str:
    return f"processing videos|{action}|{video_key}|{chunk_index}|{file_index}"


def selective_process_videos(
    *,
    info: dict[str, Any],
    episodes_df: pd.DataFrame,
    video_keys: list[str],
    src_root: Path,
    dst_root: Path,
    kept_episode_indices: list[int],
    old_to_new: dict[int, int],
    fps: float,
    on_progress: VideoProgressCallback | None = None,
) -> tuple[dict[int, dict[str, Any]], dict[str, int]]:
    """Copy unchanged chunk files and re-encode mixed chunk files.

    Returns:
        video_metadata: new_episode_index -> {videos/... column updates}
        stats: {"copied": N, "reencoded": M, "skipped": K}
    """
    kept_set = set(kept_episode_indices)
    video_metadata: dict[int, dict[str, Any]] = {}
    stats = {"copied": 0, "reencoded": 0, "skipped": 0}
    video_files = iter_video_files(episodes_df, video_keys)
    total_files = len(video_files)

    for file_number, (video_key, chunk_index, file_index) in enumerate(video_files, start=1):
        all_in_file = episodes_in_video_file(episodes_df, video_key, chunk_index, file_index)
        kept_in_file = [episode_index for episode_index in all_in_file if episode_index in kept_set]

        if not kept_in_file:
            if on_progress is not None:
                on_progress(file_number, total_files, "skip", video_key, chunk_index, file_index)
            stats["skipped"] += 1
            continue

        rel_path = video_rel_path(info, video_key, chunk_index, file_index)
        src_path = src_root / rel_path
        dst_path = dst_root / rel_path
        if not src_path.is_file():
            alt_suffix = ".mkv" if src_path.suffix.lower() == ".mp4" else ".mp4"
            alt_path = src_path.with_suffix(alt_suffix)
            if alt_path.is_file():
                src_path = alt_path
                dst_path = dst_path.with_suffix(alt_suffix)
            else:
                raise FileNotFoundError(f"Source video not found: {src_path}")

        if set(all_in_file) == set(kept_in_file):
            if on_progress is not None:
                on_progress(file_number, total_files, "copy", video_key, chunk_index, file_index)
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            stats["copied"] += 1
            continue

        sorted_keep = sorted(kept_in_file, key=lambda episode_index: old_to_new[episode_index])
        frame_ranges: list[tuple[int, int]] = []
        for old_index in sorted_keep:
            row = episodes_df[episodes_df["episode_index"] == old_index].iloc[0]
            from_frame = round(float(row[f"videos/{video_key}/from_timestamp"]) * fps)
            to_frame = round(float(row[f"videos/{video_key}/to_timestamp"]) * fps)
            length = int(row["length"])
            if length != to_frame - from_frame:
                logger.warning(
                    "Episode %s %s length mismatch: parquet=%s video_frames=%s",
                    old_index,
                    video_key,
                    length,
                    to_frame - from_frame,
                )
            frame_ranges.append((from_frame, to_frame))

        vcodec, pix_fmt = video_encode_params(info, video_key)
        if on_progress is not None:
            on_progress(file_number, total_files, "reencode", video_key, chunk_index, file_index)
        logger.info(
            "Re-encoding mixed video %s chunk=%s file=%s (%d kept / %d total episodes)",
            video_key,
            chunk_index,
            file_index,
            len(kept_in_file),
            len(all_in_file),
        )
        keep_episodes_from_video(src_path, dst_path, frame_ranges, fps, vcodec, pix_fmt)
        stats["reencoded"] += 1

        cumulative_ts = 0.0
        for old_index in sorted_keep:
            new_index = old_to_new[old_index]
            row = episodes_df[episodes_df["episode_index"] == old_index].iloc[0]
            ep_duration = int(row["length"]) / fps
            entry = video_metadata.setdefault(new_index, {})
            entry[f"videos/{video_key}/chunk_index"] = chunk_index
            entry[f"videos/{video_key}/file_index"] = file_index
            entry[f"videos/{video_key}/from_timestamp"] = cumulative_ts
            entry[f"videos/{video_key}/to_timestamp"] = cumulative_ts + ep_duration
            cumulative_ts += ep_duration

    return video_metadata, stats
