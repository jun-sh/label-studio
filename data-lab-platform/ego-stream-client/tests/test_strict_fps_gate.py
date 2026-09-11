"""Tests for strict 30Hz MCAP fps gate."""

from __future__ import annotations

from strict_fps_gate import (
    analyze_timestamp_series,
    strict_timestamp_series_ok,
    timeline_from_writer_spans,
    timeline_grace_applies,
    timeline_integrity_issues,
)


def test_strict_timestamp_series_ok_at_30hz() -> None:
    interval_ns = 33_333_333
    interval_ms = 1000.0 / 30.0
    ts = [1_000_000_000 + i * interval_ns for i in range(120)]
    report = analyze_timestamp_series(ts, interval_ms=interval_ms)
    assert report["eff_hz"] > 29.8
    assert report["eff_hz"] < 30.2
    assert report["eff_hz_ok"] is True
    assert strict_timestamp_series_ok(report, interval_ms=interval_ms)


def test_strict_timestamp_series_rejects_legacy_33ms_grid() -> None:
    ts = [i * 33_000_000 for i in range(100)]
    interval_ms = 1000.0 / 30.0
    report = analyze_timestamp_series(ts, interval_ms=interval_ms)
    assert report["eff_hz_ok"] is False
    assert not strict_timestamp_series_ok(report, interval_ms=interval_ms)


def test_timeline_from_writer_spans_ok_at_30hz() -> None:
    frames = 300
    grid_min = 1_000_000_000
    grid_max = grid_min + int(frames * 33_333_333)
    imu_min = grid_min + 5_000_000
    imu_max = imu_min + int(frames * 33_333_333)
    timeline = timeline_from_writer_spans(
        frame_count=frames,
        grid_min_ns=grid_min,
        grid_max_ns=grid_max,
        imu_min_ns=imu_min,
        imu_max_ns=imu_max,
        imu_samples=frames * 6,
    )
    assert timeline["ok"] is True
    assert timeline["source"] == "writer_spans"
    assert not timeline_integrity_issues(timeline)


def test_timeline_from_writer_spans_flags_compression() -> None:
    frames = 300
    grid_min = 1_000_000_000
    grid_max = grid_min + int(frames * 33_333_333)
    imu_min = grid_min
    imu_max = imu_min + int(frames * 38_000_000)
    timeline = timeline_from_writer_spans(
        frame_count=frames,
        grid_min_ns=grid_min,
        grid_max_ns=grid_max,
        imu_min_ns=imu_min,
        imu_max_ns=imu_max,
        imu_samples=frames * 6,
    )
    assert timeline["ok"] is False
    issues = timeline_integrity_issues(timeline)
    assert issues
    assert issues[0].startswith("timeline_incoherent:")


def test_timeline_grace_skips_short_stop_tail() -> None:
    """Stop-truncated tails (e.g. 72 frames) have noisy ratio — do not reject."""
    frames = 72
    grid_min = 1_000_000_000
    grid_max = grid_min + int(frames * 33_333_333)
    imu_min = grid_min
    imu_max = imu_min + int(frames * 38_000_000)
    timeline = timeline_from_writer_spans(
        frame_count=frames,
        grid_min_ns=grid_min,
        grid_max_ns=grid_max,
        imu_min_ns=imu_min,
        imu_max_ns=imu_max,
        imu_samples=frames * 6,
    )
    assert timeline["ok"] is False
    assert timeline_grace_applies(timeline, frame_count=frames)
    assert not timeline_integrity_issues(timeline, frame_count=frames)


def test_timeline_grace_does_not_mask_full_segment_compression() -> None:
    frames = 1800
    grid_min = 1_000_000_000
    grid_max = grid_min + int(frames * 33_333_333)
    imu_min = grid_min
    imu_max = imu_min + int(frames * 38_000_000)
    timeline = timeline_from_writer_spans(
        frame_count=frames,
        grid_min_ns=grid_min,
        grid_max_ns=grid_max,
        imu_min_ns=imu_min,
        imu_max_ns=imu_max,
        imu_samples=frames * 6,
    )
    assert not timeline_grace_applies(timeline, frame_count=frames)
    issues = timeline_integrity_issues(timeline, frame_count=frames)
    assert issues
    assert issues[0].startswith("timeline_incoherent:")


def test_strict_timestamp_series_fail_when_sparse() -> None:
    interval_ns = 100_000_000
    ts = [1_000_000_000 + i * interval_ns for i in range(30)]
    report = analyze_timestamp_series(ts, interval_ms=1000.0 / 30.0)
    assert not strict_timestamp_series_ok(report, interval_ms=1000.0 / 30.0)
