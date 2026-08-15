"""Tests for reliable post-upload segment deletion."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from segment_store import (
    delete_segment_dir,
    list_uploaded_segment_dirs,
    mark_segment_uploaded,
    purge_uploaded_segments,
    schedule_segment_delete,
    wait_for_segment_deletes,
)


def _make_segment(root: Path, session_id: str, segment_id: str, *, uploaded: bool = False) -> Path:
    seg = root / "sessions" / session_id / "segments" / segment_id
    (seg / "frames").mkdir(parents=True)
    (seg / "frames" / "00000000.bin").write_bytes(b"frame")
    manifest = {
        "session_id": session_id,
        "segment_id": segment_id,
        "closed": True,
        "uploaded": uploaded,
        "frame_count": 1,
    }
    (seg / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    return seg


def test_delete_segment_dir_removes_tree(tmp_path: Path) -> None:
    seg = _make_segment(tmp_path, "sess_a", "seg_000001")
    assert delete_segment_dir(seg) is True
    assert not seg.exists()


def test_mark_segment_uploaded_sync_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import segment_store

    monkeypatch.setattr(segment_store, "SEGMENT_ASYNC_DELETE", False)
    seg = _make_segment(tmp_path, "sess_a", "seg_000002")
    mark_segment_uploaded(seg, delete=True)
    assert not seg.exists()


def test_purge_uploaded_segments(tmp_path: Path) -> None:
    keep = _make_segment(tmp_path, "sess_a", "seg_000010", uploaded=False)
    drop = _make_segment(tmp_path, "sess_a", "seg_000011", uploaded=True)
    purged, failed = purge_uploaded_segments(tmp_path, "sess_a")
    assert purged == 1
    assert failed == 0
    assert keep.exists()
    assert not drop.exists()


def test_list_uploaded_segment_dirs(tmp_path: Path) -> None:
    _make_segment(tmp_path, "sess_a", "seg_000020", uploaded=True)
    _make_segment(tmp_path, "sess_a", "seg_000021", uploaded=False)
    uploaded = list_uploaded_segment_dirs(tmp_path, "sess_a")
    assert [p.name for p in uploaded] == ["seg_000020"]


def test_async_delete_worker_drains(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import segment_store

    monkeypatch.setattr(segment_store, "SEGMENT_ASYNC_DELETE", True)
    seg = _make_segment(tmp_path, "sess_a", "seg_000030")
    schedule_segment_delete(seg)
    assert wait_for_segment_deletes(timeout_s=5.0) is True
    assert not seg.exists()
