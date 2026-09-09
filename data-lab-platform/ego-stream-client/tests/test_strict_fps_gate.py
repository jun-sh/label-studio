"""Tests for strict 30Hz MCAP fps gate."""

from __future__ import annotations

from strict_fps_gate import analyze_timestamp_series, strict_timestamp_series_ok


def test_strict_timestamp_series_ok_at_30hz() -> None:
    interval_ns = 33_333_333
    ts = [1_000_000_000 + i * interval_ns for i in range(120)]
    report = analyze_timestamp_series(ts, interval_ms=33.0)
    assert report["eff_hz"] > 29.8
    assert report["eff_hz"] < 30.2
    assert strict_timestamp_series_ok(report, interval_ms=33.0)


def test_strict_timestamp_series_fail_when_sparse() -> None:
    interval_ns = 100_000_000
    ts = [1_000_000_000 + i * interval_ns for i in range(30)]
    report = analyze_timestamp_series(ts, interval_ms=33.0)
    assert not strict_timestamp_series_ok(report, interval_ms=33.0)
