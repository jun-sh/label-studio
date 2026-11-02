"""Tests for MCAP preflight validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcap_preflight import (
    McapPreflightError,
    preflight_enabled,
    preflight_segment_dir,
    preflight_segment_dir_or_raise,
)


def test_preflight_enabled_default() -> None:
    assert preflight_enabled() is True


def test_preflight_missing_mcap(tmp_path: Path) -> None:
    seg = tmp_path / "seg_000001"
    seg.mkdir()
    result = preflight_segment_dir(seg)
    assert result["ok"] is False
    assert "missing_segment_mcap" in result["issues"]


def test_preflight_or_raise_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGO_MCAP_PREFLIGHT", "1")
    seg = tmp_path / "seg_000001"
    seg.mkdir()
    with pytest.raises(McapPreflightError):
        preflight_segment_dir_or_raise(seg)


def test_preflight_skipped_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGO_MCAP_PREFLIGHT", "0")
    seg = tmp_path / "seg_000001"
    seg.mkdir()
    out = preflight_segment_dir_or_raise(seg)
    assert out.get("skipped") is True


def test_preflight_manifest_warning_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    golden = (
        Path(__file__).resolve().parents[2]
        / "lerobot-studio"
        / "fixtures"
        / "mcap"
        / "golden-seg.mcap"
    )
    if not golden.is_file():
        pytest.skip("golden mcap fixture missing")
    monkeypatch.setenv("EGO_MCAP_PREFLIGHT", "1")
    seg = tmp_path / "seg_000001"
    seg.mkdir()
    (seg / "segment.mcap").write_bytes(golden.read_bytes())
    (seg / "manifest.json").write_text(
        json.dumps({"frame_count": 999, "session_id": "sess_x", "segment_id": "seg_000001"}) + "\n",
        encoding="utf-8",
    )
    result = preflight_segment_dir(seg)
    assert result["ok"] is True
    assert any("manifest_frame_mismatch" in w for w in result.get("warnings", []))
