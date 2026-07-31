"""Tests for delivery video trim validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import video_service


def test_expected_video_duration() -> None:
    assert video_service.expected_video_duration(94, 5.0) == 18.8
    assert video_service.expected_video_duration(141, 5.0) == 28.2


def test_duration_within_tolerance_strict() -> None:
    assert video_service.duration_within_tolerance(18.8, 18.8, 5.0, strict=True)
    assert video_service.duration_within_tolerance(18.801, 18.8, 5.0, strict=True)
    assert not video_service.duration_within_tolerance(18.9, 18.8, 5.0, strict=True)


def test_duration_within_tolerance_streaming() -> None:
    assert video_service.duration_within_tolerance(18.9, 18.8, 5.0, strict=False)
    assert not video_service.duration_within_tolerance(19.1, 18.8, 5.0, strict=False)


def test_delivery_tmp_path_uses_valid_mp4_suffix(tmp_path: Path) -> None:
    out = tmp_path / "file-047.mp4"
    tmp = video_service._delivery_tmp_path(out)
    assert tmp.name == "file-047.tmp.mp4"
    assert tmp.suffix == ".mp4"


def test_trim_video_for_delivery_reencodes_when_copy_trim_is_long(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    dst = tmp_path / "dst.mp4"
    src.write_bytes(b"source")

    with (
        patch.object(video_service, "trim_video_with_ffmpeg", return_value=True),
        patch.object(video_service, "validate_delivery_video", side_effect=[False, True]),
        patch.object(
            video_service,
            "trim_video_reencode_frames",
            return_value=True,
        ) as reencode_frames,
    ):
        ok = video_service.trim_video_for_delivery(src, dst, 10.0, 28.8, 144, 5.0)

    assert ok
    reencode_frames.assert_called_once_with(src, dst, 10.0, 144, 5.0)


def test_trim_video_for_delivery_skips_reencode_when_aligned(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    dst = tmp_path / "dst.mp4"
    src.write_bytes(b"source")

    with (
        patch.object(video_service, "trim_video_with_ffmpeg", return_value=True),
        patch.object(video_service, "validate_delivery_video", return_value=True),
        patch.object(video_service, "trim_video_reencode_frames") as reencode_frames,
    ):
        ok = video_service.trim_video_for_delivery(src, dst, 10.0, 28.8, 144, 5.0)

    assert ok
    reencode_frames.assert_not_called()


def test_validate_delivery_video_checks_frame_count(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"clip")
    with (
        patch.object(video_service, "get_video_duration", return_value=18.8),
        patch.object(video_service, "get_video_frame_count", return_value=95),
    ):
        assert not video_service.validate_delivery_video(path, 94, 5.0)

    with (
        patch.object(video_service, "get_video_duration", return_value=18.8),
        patch.object(video_service, "get_video_frame_count", return_value=94),
    ):
        assert video_service.validate_delivery_video(path, 94, 5.0)
