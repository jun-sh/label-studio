"""Tests for selective video delivery processing."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from video_delivery import (
    _ffmpeg_video_encode_args,
    episodes_in_video_file,
    format_video_progress,
    selective_process_videos,
)


def _write_minimal_episodes(path: Path) -> pd.DataFrame:
    episodes = pd.DataFrame(
        [
            {
                "episode_index": 0,
                "length": 5,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 1.0,
            },
            {
                "episode_index": 1,
                "length": 5,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 1.0,
                "videos/observation.images.top/to_timestamp": 2.0,
            },
            {
                "episode_index": 2,
                "length": 5,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 2.0,
                "videos/observation.images.top/to_timestamp": 3.0,
            },
        ]
    )
    pq.write_table(pa.Table.from_pandas(episodes, preserve_index=False), path)
    return episodes


def test_episodes_in_video_file(tmp_path: Path) -> None:
    episodes_path = tmp_path / "episodes.parquet"
    episodes = _write_minimal_episodes(episodes_path)
    result = episodes_in_video_file(episodes, "observation.images.top", 0, 0)
    assert result == [0, 1, 2]


@patch("video_delivery.keep_episodes_from_video")
def test_selective_process_videos_reindexes_mixed_file(mock_reencode, tmp_path: Path) -> None:
    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    video_path = src_root / "videos/observation.images.top/chunk-000/file-000.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"video-bytes")

    def _fake_reencode(src: Path, dst: Path, ranges, fps, vcodec, pix_fmt) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"reencoded")

    mock_reencode.side_effect = _fake_reencode

    episodes = _write_minimal_episodes(tmp_path / "episodes.parquet")
    info = {
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": {
            "observation.images.top": {
                "dtype": "video",
                "info": {"video.codec": "h264", "video.pix_fmt": "yuv420p"},
            }
        },
    }

    metadata, stats = selective_process_videos(
        info=info,
        episodes_df=episodes,
        video_keys=["observation.images.top"],
        src_root=src_root,
        dst_root=dst_root,
        kept_episode_indices=[0, 2],
        old_to_new={0: 0, 2: 1},
        fps=5.0,
    )

    assert stats == {"copied": 0, "reencoded": 1, "skipped": 0, "resumed": 0}
    assert mock_reencode.call_count == 1
    assert (dst_root / "videos/observation.images.top/chunk-000/file-000.mp4").read_bytes() == b"reencoded"
    assert metadata[0]["videos/observation.images.top/from_timestamp"] == 0.0
    assert metadata[0]["videos/observation.images.top/to_timestamp"] == 1.0
    assert metadata[1]["videos/observation.images.top/from_timestamp"] == 1.0
    assert metadata[1]["videos/observation.images.top/to_timestamp"] == 2.0


@patch("video_delivery.keep_episodes_from_video")
def test_selective_process_videos_copies_pure_file(mock_reencode, tmp_path: Path) -> None:
    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    video_path = src_root / "videos/observation.images.top/chunk-000/file-001.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"pure-copy")

    episodes = pd.DataFrame(
        [
            {
                "episode_index": 10,
                "length": 4,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 1,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 0.8,
            }
        ]
    )
    info = {
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": {
            "observation.images.top": {
                "dtype": "video",
                "info": {"video.codec": "h264", "video.pix_fmt": "yuv420p"},
            }
        },
    }

    metadata, stats = selective_process_videos(
        info=info,
        episodes_df=episodes,
        video_keys=["observation.images.top"],
        src_root=src_root,
        dst_root=dst_root,
        kept_episode_indices=[10],
        old_to_new={10: 0},
        fps=5.0,
    )

    assert stats == {"copied": 1, "reencoded": 0, "skipped": 0, "resumed": 0}
    mock_reencode.assert_not_called()
    assert metadata == {}
    assert (dst_root / "videos/observation.images.top/chunk-000/file-001.mp4").read_bytes() == b"pure-copy"


@patch("video_delivery.keep_episodes_from_video")
def test_selective_process_videos_reports_progress(mock_reencode, tmp_path: Path) -> None:
    src_root = tmp_path / "src"
    dst_root = tmp_path / "dst"
    for file_index, payload in enumerate((b"pure-copy", b"mixed", b"pure-copy-2")):
        video_path = src_root / f"videos/observation.images.top/chunk-000/file-{file_index:03d}.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(payload)

    def _fake_reencode(src: Path, dst: Path, ranges, fps, vcodec, pix_fmt) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"reencoded")

    mock_reencode.side_effect = _fake_reencode

    episodes = pd.DataFrame(
        [
            {
                "episode_index": 0,
                "length": 5,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 1.0,
            },
            {
                "episode_index": 1,
                "length": 5,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 1,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 1.0,
            },
            {
                "episode_index": 2,
                "length": 5,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 1,
                "videos/observation.images.top/from_timestamp": 1.0,
                "videos/observation.images.top/to_timestamp": 2.0,
            },
            {
                "episode_index": 3,
                "length": 5,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 2,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 1.0,
            },
        ]
    )
    info = {
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": {
            "observation.images.top": {
                "dtype": "video",
                "info": {"video.codec": "h264", "video.pix_fmt": "yuv420p"},
            }
        },
    }
    progress_events: list[tuple[int, int, str, str, int, int]] = []

    def _on_progress(current, total, action, video_key, chunk_index, file_index) -> None:
        progress_events.append((current, total, action, video_key, chunk_index, file_index))

    metadata, stats = selective_process_videos(
        info=info,
        episodes_df=episodes,
        video_keys=["observation.images.top"],
        src_root=src_root,
        dst_root=dst_root,
        kept_episode_indices=[0, 1, 3],
        old_to_new={0: 0, 1: 1, 3: 2},
        fps=5.0,
        on_progress=_on_progress,
    )

    assert stats == {"copied": 2, "reencoded": 1, "skipped": 0, "resumed": 0}
    assert metadata
    assert progress_events == [
        (1, 3, "copy", "observation.images.top", 0, 0),
        (2, 3, "reencode", "observation.images.top", 0, 1),
        (3, 3, "copy", "observation.images.top", 0, 2),
    ]
    assert format_video_progress("reencode", "observation.images.top", 0, 1) == (
        "processing videos|reencode|observation.images.top|0|1"
    )


def test_ffmpeg_video_encode_args_prefers_source_bitrate() -> None:
    args = _ffmpeg_video_encode_args("libx264", "yuv420p", 1_500_000)
    assert "-b:v" in args
    assert "1500000" in args
    assert "-crf" not in args


def test_ffmpeg_video_encode_args_falls_back_to_crf() -> None:
    args = _ffmpeg_video_encode_args("libx264", "yuv420p", None)
    assert "-crf" in args
    assert "23" in args



def _encode_test_video(dst: Path, *codec_args: str) -> None:
    import subprocess

    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=32x32:rate=5", "-frames:v", "3", *codec_args, str(dst)],
        check=True,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_encode_params_follow_source_file(tmp_path: Path) -> None:
    from video_delivery import video_encode_params

    depth = tmp_path / "depth.mkv"
    _encode_test_video(depth, "-c:v", "ffv1", "-pix_fmt", "gray16le")
    undeclared = {"features": {"d": {"dtype": "video", "info": {"video.is_depth_map": True}}}}
    # Without metadata the old default (h264/yuv420p) would have dropped depth to 8 bit.
    assert video_encode_params(undeclared, "d", depth) == ("ffv1", "gray16le")
    wrong = {"features": {"d": {"dtype": "video", "info": {"video.codec": "h264", "video.pix_fmt": "yuv420p"}}}}
    assert video_encode_params(wrong, "d", depth) == ("ffv1", "gray16le")

    rgb = tmp_path / "rgb.mp4"
    _encode_test_video(rgb, "-c:v", "libx264", "-pix_fmt", "yuv420p")
    assert video_encode_params({}, "top", rgb) == ("libx264", "yuv420p")


def test_encode_params_fall_back_to_metadata_when_unprobeable(tmp_path: Path) -> None:
    from video_delivery import video_encode_params

    garbage = tmp_path / "x.mkv"
    garbage.write_bytes(b"not a video")
    declared = {"features": {"d": {"info": {"video.codec": "ffv1", "video.pix_fmt": "gray16le"}}}}
    assert video_encode_params(declared, "d", garbage) == ("ffv1", "gray16le")
    assert video_encode_params(declared, "d") == ("ffv1", "gray16le")
    assert video_encode_params({}, "d", tmp_path / "missing.mp4") == ("libx264", "yuv420p")
