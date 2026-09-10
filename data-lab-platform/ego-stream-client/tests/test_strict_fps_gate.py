"""Tests for strict 30Hz MCAP fps gate."""

from __future__ import annotations

from strict_fps_gate import (
    analyze_timestamp_series,
    strict_timestamp_series_ok,
    timeline_from_writer_spans,
    timeline_integrity_issues,
)


def test_strict_timestamp_series_ok_at_30hz() -> None:
    interval_ns = 33_333_333
    ts = [1_000_000_000 + i * interval_ns for i in range(120)]
    report = analyze_timestamp_series(ts, interval_ms=33.0)
    assert report["eff_hz"] > 29.8
    assert report["eff_hz"] < 30.2
    assert strict_timestamp_series_ok(report, interval_ms=33.0)


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


def test_strict_timestamp_series_fail_when_sparse() -> None:
    interval_ns = 100_000_000
    ts = [1_000_000_000 + i * interval_ns for i in range(30)]
    report = analyze_timestamp_series(ts, interval_ms=33.0)
    assert not strict_timestamp_series_ok(report, interval_ms=33.0)
