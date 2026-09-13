"""Phase 1 tests for segment_store manifest v2 state machine and GC rules."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from segment_store import (
    MANIFEST_SCHEMA_VERSION,
    build_session_seal,
    check_segment_integrity,
    check_segment_upload_qc,
    clear_segment_uploaded,
    finalize_segment_manifest_after_persist,
    flush_pending_segment_deletes,
    gc_segment_dir,
    list_closed_pending_segments,
    manifest_status,
    mark_segment_uploaded,
    purge_uploaded_segments,
    read_manifest,
    read_session_seal,
    reconcile_orphan_active_segment,
    scan_orphan_active_segments,
    sealed_segment_total,
    write_manifest_v2,
    write_session_seal,
    can_gc_segment,
)


def _write_v1_manifest(
    segment_dir: Path,
    *,
    closed: bool = True,
    uploaded: bool = False,
    frame_count: int = 2,
) -> None:
    segment_dir.mkdir(parents=True, exist_ok=True)
    (segment_dir / "frames").mkdir(exist_ok=True)
    manifest = {
        "segment_id": segment_dir.name,
        "session_id": "sess_v1",
        "start_frame_index": 0,
        "end_frame_index": frame_count - 1,
        "frame_count": frame_count,
        "closed": closed,
        "uploaded": uploaded,
        "created_at": "2026-08-18T00:00:00Z",
    }
    (segment_dir / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")


def _write_valid_segment(
    segment_dir: Path,
    *,
    frame_count: int = 2,
    session_id: str = "sess_test",
    status: str = "CLOSED",
    with_imu: bool = True,
) -> None:
    segment_dir.mkdir(parents=True, exist_ok=True)
    (segment_dir / "frames").mkdir(exist_ok=True)
    rows = [
        {"frame_index": i, "timestamp_ns": i + 1, "task": "t", "observation.state": [0.0] * 6}
        for i in range(frame_count)
    ]
    (segment_dir / "rows.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    for i in range(frame_count):
        (segment_dir / "frames" / f"{i:08d}.bin").write_bytes(b"DLB1" + bytes([i % 256]))
    if with_imu:
        (segment_dir / "imu_raw.jsonl").write_text(
            json.dumps({"ts_ns": 1, "sensor": "gyro", "x": 0.0, "y": 0.0, "z": 0.0}) + "\n",
            encoding="utf-8",
        )
    write_manifest_v2(
        segment_dir,
        {
            "segment_id": segment_dir.name,
            "session_id": session_id,
            "start_frame_index": 0,
            "end_frame_index": frame_count - 1,
            "frame_count": frame_count,
            "status": status,
            "created_at": "2026-08-18T00:00:00Z",
            "closed_at": "2026-08-18T00:00:10Z",
            "upload": {
                "attempts": 0,
                "last_attempt_at": None,
                "last_error": None,
                "remote_ack_at": None,
            },
            "integrity": {"checked_at": "2026-08-18T00:00:10Z", "ok": True, "issues": []},
        },
    )


def test_manifest_v2_write_has_no_legacy_bools(tmp_path: Path) -> None:
    seg = tmp_path / "seg_000001"
    _write_valid_segment(seg)
    raw = json.loads((seg / "manifest.json").read_text(encoding="utf-8"))
    assert raw["manifest_schema_version"] == MANIFEST_SCHEMA_VERSION
    assert raw["status"] == "CLOSED"
    assert "closed" not in raw
    assert "uploaded" not in raw


def test_manifest_v1_read_compat_mapping() -> None:
    assert manifest_status({"closed": False, "uploaded": False}) == "RECORDING"
    assert manifest_status({"closed": True, "uploaded": False}) == "CLOSED"
    assert manifest_status({"closed": True, "uploaded": True}) == "UPLOADED"


def test_normal_segment_integrity_and_finalize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGO_STATION_ID", "ego-001")
    seg = tmp_path / "seg_000001"
    (seg / "frames").mkdir(parents=True)
    write_manifest_v2(
        seg,
        {
            "segment_id": "seg_000001",
            "session_id": "sess_test",
            "start_frame_index": 0,
            "end_frame_index": 1,
            "frame_count": 2,
            "status": "RECORDING",
            "created_at": "2026-08-18T00:00:00Z",
            "upload": {"attempts": 0, "last_attempt_at": None, "last_error": None, "remote_ack_at": None},
            "integrity": {"checked_at": "2026-08-18T00:00:00Z", "ok": True, "issues": []},
        },
    )
    rows = [
        {"frame_index": 0, "timestamp_ns": 1, "task": "t", "observation.state": [0.0] * 6},
        {"frame_index": 1, "timestamp_ns": 2, "task": "t", "observation.state": [0.0] * 6},
    ]
    (seg / "rows.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (seg / "frames" / "00000000.bin").write_bytes(b"bin0")
    (seg / "frames" / "00000001.bin").write_bytes(b"bin1")
    (seg / "imu_raw.jsonl").write_text(
        json.dumps({"ts_ns": 1, "sensor": "accel", "x": 0.0, "y": 0.0, "z": 9.8}) + "\n",
        encoding="utf-8",
    )

    ok, issues = check_segment_integrity(seg)
    assert ok, issues
    status = finalize_segment_manifest_after_persist(seg)
    assert status == "CLOSED"
    manifest = read_manifest(seg)
    assert manifest["status"] == "CLOSED"
    assert manifest["integrity"]["ok"] is True


def test_corrupt_segment_missing_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGO_STATION_ID", "ego-001")
    seg = tmp_path / "seg_corrupt"
    _write_valid_segment(seg, frame_count=3)
    (seg / "frames" / "00000002.bin").unlink()

    ok, issues = check_segment_integrity(seg)
    assert not ok
    assert any("frame_bin_count_mismatch" in i for i in issues)

    status = finalize_segment_manifest_after_persist(seg)
    assert status == "CORRUPT"
    assert read_manifest(seg)["status"] == "CORRUPT"


def test_orphan_active_reconcile_marks_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGO_STATION_ID", "ego-001")
    active_root = tmp_path / "active"
    seg = active_root / "sessions" / "sess_x" / "segments" / "seg_orphan"
    seg.mkdir(parents=True)
    write_manifest_v2(
        seg,
        {
            "segment_id": "seg_orphan",
            "session_id": "sess_x",
            "start_frame_index": 0,
            "end_frame_index": 0,
            "frame_count": 1,
            "status": "RECORDING",
            "created_at": "2026-08-18T00:00:00Z",
            "upload": {"attempts": 0, "last_attempt_at": None, "last_error": None, "remote_ack_at": None},
            "integrity": {"checked_at": "2026-08-18T00:00:00Z", "ok": False, "issues": []},
        },
    )
    (seg / "rows.jsonl").write_text("{}\n", encoding="utf-8")

    orphans = scan_orphan_active_segments(active_root, "sess_x")
    assert seg in orphans

    status = reconcile_orphan_active_segment(seg)
    assert status == "CORRUPT"
    manifest = read_manifest(seg)
    assert "orphan_active" in manifest["integrity"]["issues"]


def test_gc_only_uploaded_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import segment_store as ss

    monkeypatch.setattr(ss, "SEGMENT_ASYNC_DELETE", False)
    root = tmp_path / "segments"
    session_id = "sess_gc"
    closed = root / "sessions" / session_id / "segments" / "seg_closed"
    uploaded = root / "sessions" / session_id / "segments" / "seg_uploaded"
    _write_valid_segment(closed, session_id=session_id, status="CLOSED")
    _write_valid_segment(uploaded, session_id=session_id, status="UPLOADED")

    assert not can_gc_segment(closed)
    assert can_gc_segment(uploaded)

    with pytest.raises(RuntimeError, match="refusing to delete"):
        gc_segment_dir(closed)

    gc_segment_dir(uploaded)
    assert not uploaded.exists()
    assert closed.exists()


def test_list_closed_pending_v1_and_v2(tmp_path: Path) -> None:
    root = tmp_path / "segments"
    session_id = "sess_list"
    v1 = root / "sessions" / session_id / "segments" / "seg_v1"
    v2 = root / "sessions" / session_id / "segments" / "seg_v2"
    failed = root / "sessions" / session_id / "segments" / "seg_fail"
    _write_v1_manifest(v1, closed=True, uploaded=False)
    manifest = json.loads((v1 / "manifest.json").read_text(encoding="utf-8"))
    manifest["session_id"] = session_id
    (v1 / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    _write_valid_segment(v2, session_id=session_id, status="CLOSED")
    _write_valid_segment(failed, session_id=session_id, status="UPLOAD_FAILED")

    pending = list_closed_pending_segments(root, session_id)
    names = {p.name for p in pending}
    assert "seg_v1" in names
    assert "seg_v2" in names
    assert "seg_fail" in names


def test_mark_uploaded_and_clear_for_force_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import segment_store as ss

    monkeypatch.setattr(ss, "SEGMENT_ASYNC_DELETE", False)
    seg = tmp_path / "seg_000001"
    _write_valid_segment(seg, status="CLOSED")

    mark_segment_uploaded(seg)
    assert read_manifest(seg)["status"] == "UPLOADED"
    assert read_manifest(seg)["upload"]["remote_ack_at"] is not None

    clear_segment_uploaded(seg)
    assert read_manifest(seg)["status"] == "CLOSED"
    assert read_manifest(seg)["upload"]["remote_ack_at"] is None

    mark_segment_uploaded(seg, delete=True)
    assert not seg.exists()


def test_mark_uploaded_delete_sync_even_when_async_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import segment_store as ss

    monkeypatch.setattr(ss, "SEGMENT_ASYNC_DELETE", True)
    seg = tmp_path / "seg_000001"
    _write_valid_segment(seg, status="CLOSED")

    mark_segment_uploaded(seg, delete=True)
    assert not seg.exists()


def test_purge_uploaded_segments_strict(tmp_path: Path) -> None:
    root = tmp_path / "segments"
    session_id = "sess_purge"
    uploaded = root / "sessions" / session_id / "segments" / "seg_uploaded"
    closed = root / "sessions" / session_id / "segments" / "seg_closed"
    _write_valid_segment(uploaded, session_id=session_id, status="UPLOADED")
    _write_valid_segment(closed, session_id=session_id, status="CLOSED")

    removed = purge_uploaded_segments(root, session_id, strict=True)
    assert removed == 1
    assert not uploaded.exists()
    assert closed.exists()


def test_flush_pending_segment_deletes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import segment_store as ss

    monkeypatch.setattr(ss, "SEGMENT_ASYNC_DELETE", True)
    seg = tmp_path / "seg_async"
    _write_valid_segment(seg, status="UPLOADED")

    gc_segment_dir(seg, sync=False)
    assert seg.exists()
    assert flush_pending_segment_deletes(timeout_s=5.0) == 0
    assert not seg.exists()


def test_corrupt_cannot_mark_uploaded(tmp_path: Path) -> None:
    seg = tmp_path / "seg_bad"
    _write_valid_segment(seg, status="CORRUPT")
    with pytest.raises(ValueError, match="CORRUPT"):
        mark_segment_uploaded(seg)


def test_finalize_without_manifest_reconciles_orphan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGO_STATION_ID", "ego-001")
    seg = tmp_path / "seg_orphan_no_manifest"
    seg.mkdir(parents=True)
    (seg / "rows.jsonl").write_text("{}\n", encoding="utf-8")

    status = finalize_segment_manifest_after_persist(seg)
    assert status == "CORRUPT"
    manifest = read_manifest(seg)
    assert manifest["status"] == "CORRUPT"
    assert "orphan_active" in manifest["integrity"]["issues"]


def test_session_seal_complete_and_total(tmp_path: Path) -> None:
    session_id = "sess_seal_test"
    seg_root = tmp_path / "sessions" / session_id / "segments" / "seg_000"
    _write_valid_segment(seg_root, status="CLOSED")
    seal = build_session_seal(tmp_path, session_id, complete=True)
    assert seal["segment_count"] == 1
    assert seal["complete"] is True
    write_session_seal(tmp_path, session_id, complete=True)
    loaded = read_session_seal(tmp_path, session_id)
    assert loaded is not None
    assert loaded["segment_count"] == 1
    assert sealed_segment_total(tmp_path, session_id) == 1
    assert sealed_segment_total(tmp_path, "sess_missing") is None


def test_capture_close_timeline_qc_warn_not_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QA layering: bad timeline → CLOSED + qc_ok=false, not CORRUPT at capture close."""
    monkeypatch.setenv("EGO_STATION_ID", "ego-001")
    monkeypatch.setenv("EGO_TIMELINE_CAPTURE_BLOCK", "0")
    seg = tmp_path / "seg_timeline"
    _write_valid_segment(seg, frame_count=350, session_id="sess_tl")
    manifest = read_manifest(seg)
    manifest["timeline"] = {
        "source": "writer_spans",
        "ratio": 1.05,
        "real_fps": 28.5,
        "claimed_fps": 30.0,
        "grid_span_s": 11.67,
        "imu_span_s": 12.25,
        "ok": False,
    }
    write_manifest_v2(seg, manifest)
    ok, issues = check_segment_integrity(seg, qa_layer="capture")
    assert ok, issues
    assert not issues
    status = finalize_segment_manifest_after_persist(seg)
    assert status == "CLOSED"
    closed = read_manifest(seg)
    assert closed["integrity"]["ok"] is True
    assert closed["integrity"]["qc_ok"] is False
    assert closed["integrity"]["qc_issues"]
    qc_ok, qc_issues = check_segment_upload_qc(seg)
    assert not qc_ok
    assert qc_issues
