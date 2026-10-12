"""Tests for production H264 capture backend helpers."""

from __future__ import annotations

import os

from ego_capture_studio.capture.capture_h264_production import (
    CAPTURE_BACKEND_VERSION,
    h264_production_backend_enabled,
    parallel_drain_enabled,
)
from ego_capture_studio.capture.ingest_buffer import RingOverflowStats


def test_ring_overflow_delta_since_baseline() -> None:
    stats = RingOverflowStats()
    baseline = stats.snapshot()
    stats.note_parallel_overflow("CAM_C")
    stats.note_main_overflow("CAM_A")
    delta = stats.delta_since(baseline)
    assert delta == {"CAM_A": 1, "CAM_C": 1}


def test_parallel_drain_enabled_from_sync_mode(monkeypatch) -> None:
    monkeypatch.delenv("EGO_CAPTURE_PARALLEL_DRAIN", raising=False)
    monkeypatch.setenv("EGO_CAPTURE_SYNC_MODE", "fsync_quad")
    assert parallel_drain_enabled() is True
    assert h264_production_backend_enabled() is True


def test_parallel_drain_disabled_explicitly(monkeypatch) -> None:
    monkeypatch.setenv("EGO_CAPTURE_SYNC_MODE", "fsync_quad")
    monkeypatch.setenv("EGO_CAPTURE_PARALLEL_DRAIN", "0")
    assert parallel_drain_enabled() is False


def test_capture_backend_version_present() -> None:
    assert "fsync-quad" in CAPTURE_BACKEND_VERSION
