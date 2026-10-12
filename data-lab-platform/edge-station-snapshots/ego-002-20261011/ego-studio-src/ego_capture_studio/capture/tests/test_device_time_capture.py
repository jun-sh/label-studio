"""Device-time capture and timeline QC tests."""

from __future__ import annotations

import os

import pytest

import tests.conftest  # noqa: F401 — stubs

from capture_timestamps import resolve_commit_timestamp_ns, synthetic_grid_enabled
from strict_fps_gate import (
    analyze_timeline_coherence,
    timeline_from_writer_spans,
    timeline_integrity_issues,
)


def test_resolve_commit_timestamp_uses_device_time_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EGO_CAPTURE_SYNTHETIC_GRID", raising=False)
    assert not synthetic_grid_enabled()
    ts = resolve_commit_timestamp_ns(
        5_000_000_000,
        last_emit_ns=4_000_000_000,
        interval_ns=33_333_333,
    )
    assert ts == 5_000_000_000


def test_resolve_commit_timestamp_synthetic_grid_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGO_CAPTURE_SYNTHETIC_GRID", "1")
    ts = resolve_commit_timestamp_ns(
        5_000_000_000,
        last_emit_ns=4_000_000_000,
        interval_ns=33_333_333,
    )
    assert ts == 4_033_333_333


def test_device_span_matches_imu_passes_qc_even_at_28hz() -> None:
    """1800 frames / 63.6s real wall clock must pass when device timestamps track IMU."""
    frames = 1800
    base = 10_000_000_000_000
    interval_ns = int(63.6 / (frames - 1) * 1e9)
    device_min = base
    device_max = base + (frames - 1) * interval_ns
    imu_min = device_min + 1_000_000
    imu_max = device_max + 2_000_000
    timeline = timeline_from_writer_spans(
        frame_count=frames,
        grid_min_ns=device_min,
        grid_max_ns=device_max,
        device_min_ns=device_min,
        device_max_ns=device_max,
        imu_min_ns=imu_min,
        imu_max_ns=imu_max,
        imu_samples=frames * 6,
    )
    assert timeline["ok"] is True
    assert timeline["record_source"] == "device"
    assert abs(float(timeline["real_fps"]) - 28.3) < 0.2
    assert not timeline_integrity_issues(timeline, frame_count=frames)


def test_legacy_uniform_grid_still_flags_compression_without_device() -> None:
    frames = 1800
    grid_min = 1_000_000_000_000
    grid_max = grid_min + int((frames - 1) * 33_333_333)
    imu_min = grid_min
    imu_max = imu_min + int((frames - 1) * 38_000_000)
    grid_ns = [grid_min + i * 33_333_333 for i in range(frames)]
    imu_ns = [imu_min + i * 38_000_000 for i in range(frames)]
    timeline = analyze_timeline_coherence(grid_ns, imu_ns, [])
    assert timeline["record_source"] == "grid_uniform"
    assert timeline["ok"] is False
    assert timeline_integrity_issues(timeline, frame_count=frames)
