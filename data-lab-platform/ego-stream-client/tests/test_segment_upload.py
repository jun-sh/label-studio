"""Phase 2 tests for segment upload manifest state transitions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import segment_upload as su
from segment_store import manifest_status, read_manifest, write_manifest_v2
from segment_upload import SegmentUploader, upload_pending_segments


def _write_uploadable_segment(
    segment_dir: Path,
    *,
    session_id: str = "sess_upload",
    status: str = "CLOSED",
    v1: bool = False,
) -> None:
    segment_dir.mkdir(parents=True, exist_ok=True)
    (segment_dir / "frames").mkdir(exist_ok=True)
    frame_count = 1
    rows = [{"frame_index": 0, "timestamp_ns": 1, "task": "t", "observation.state": [0.0] * 6}]
    (segment_dir / "rows.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    (segment_dir / "frames" / "00000000.bin").write_bytes(b"DLB1\x00")
    (segment_dir / "imu_raw.jsonl").write_text(
        json.dumps({"ts_ns": 1, "sensor": "gyro", "x": 0.0, "y": 0.0, "z": 0.0}) + "\n",
        encoding="utf-8",
    )
    if v1:
        manifest = {
            "segment_id": segment_dir.name,
            "session_id": session_id,
            "start_frame_index": 0,
            "end_frame_index": 0,
            "frame_count": frame_count,
            "closed": True,
            "uploaded": False,
            "created_at": "2026-08-18T00:00:00Z",
        }
        (segment_dir / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        return
    write_manifest_v2(
        segment_dir,
        {
            "segment_id": segment_dir.name,
            "session_id": session_id,
            "start_frame_index": 0,
            "end_frame_index": 0,
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


def _mock_uploader() -> MagicMock:
    uploader = MagicMock(spec=SegmentUploader)
    uploader.protocol = "tarzst"
    uploader.upload_url = "http://example/upload"
    uploader.upload_segment_dir.return_value = {"ok": True, "framesCommitted": 1}
    return uploader


@pytest.fixture
def segment_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EGO_UPLOAD_STATUS_PATH", str(tmp_path / "upload-status.json"))
    monkeypatch.setattr(su, "DELETE_AFTER_UPLOAD", False)
    monkeypatch.setattr(su, "UPLOAD_CONCURRENCY", 1)
    return tmp_path / "segments"


def test_normal_upload_sets_uploaded(segment_root: Path) -> None:
    session_id = "sess_upload"
    seg = segment_root / "sessions" / session_id / "segments" / "seg_000001"
    _write_uploadable_segment(seg, session_id=session_id, status="CLOSED")
    uploader = _mock_uploader()

    n = upload_pending_segments(
        root=segment_root,
        session_id=session_id,
        uploader=uploader,
    )
    assert n == 1
    manifest = read_manifest(seg)
    assert manifest_status(manifest) == "UPLOADED"
    assert manifest["upload"]["remote_ack_at"] is not None
    assert manifest["upload"]["attempts"] >= 1
    uploader.upload_segment_dir.assert_called_once()


def test_network_retry_then_uploaded(segment_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(su, "UPLOAD_MAX_RETRIES", 3)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    session_id = "sess_retry"
    seg = segment_root / "sessions" / session_id / "segments" / "seg_000001"
    _write_uploadable_segment(seg, session_id=session_id, status="CLOSED")
    uploader = _mock_uploader()
    uploader.upload_segment_dir.side_effect = [
        RuntimeError("connection reset"),
        {"ok": True, "framesCommitted": 1},
    ]

    n = upload_pending_segments(root=segment_root, session_id=session_id, uploader=uploader)
    assert n == 1
    manifest = read_manifest(seg)
    assert manifest_status(manifest) == "UPLOADED"
    assert uploader.upload_segment_dir.call_count == 2
    assert manifest["upload"]["attempts"] == 2


def test_network_exhausted_sets_upload_failed(segment_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(su, "UPLOAD_MAX_RETRIES", 2)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    session_id = "sess_fail"
    seg = segment_root / "sessions" / session_id / "segments" / "seg_000001"
    _write_uploadable_segment(seg, session_id=session_id, status="CLOSED")
    uploader = _mock_uploader()
    uploader.upload_segment_dir.side_effect = RuntimeError("http 503")

    n = upload_pending_segments(root=segment_root, session_id=session_id, uploader=uploader)
    assert n == 0
    manifest = read_manifest(seg)
    assert manifest_status(manifest) == "UPLOAD_FAILED"
    assert "503" in manifest["upload"]["last_error"]
    assert manifest["upload"]["last_attempt_at"] is not None
    assert manifest["upload"]["attempts"] == 2


def test_force_reupload_clears_and_remarks_uploaded(segment_root: Path) -> None:
    session_id = "sess_force"
    seg = segment_root / "sessions" / session_id / "segments" / "seg_000001"
    _write_uploadable_segment(seg, session_id=session_id, status="UPLOADED")
    manifest = read_manifest(seg)
    manifest["upload"]["remote_ack_at"] = "2026-08-18T01:00:00Z"
    write_manifest_v2(seg, manifest)
    uploader = _mock_uploader()

    n = upload_pending_segments(
        root=segment_root,
        session_id=session_id,
        uploader=uploader,
        force=True,
    )
    assert n == 1
    manifest = read_manifest(seg)
    assert manifest_status(manifest) == "UPLOADED"
    assert manifest["upload"]["remote_ack_at"] is not None
    uploader.upload_segment_dir.assert_called_once()


def test_v1_manifest_compat_upload(segment_root: Path) -> None:
    session_id = "sess_v1"
    seg = segment_root / "sessions" / session_id / "segments" / "seg_v1"
    _write_uploadable_segment(seg, session_id=session_id, v1=True)
    uploader = _mock_uploader()

    n = upload_pending_segments(root=segment_root, session_id=session_id, uploader=uploader)
    assert n == 1
    manifest = read_manifest(seg)
    assert manifest_status(manifest) == "UPLOADED"
    assert manifest.get("manifest_schema_version") == 2


def test_corrupt_segment_skipped(segment_root: Path) -> None:
    session_id = "sess_skip"
    seg = segment_root / "sessions" / session_id / "segments" / "seg_bad"
    _write_uploadable_segment(seg, session_id=session_id, status="CORRUPT")
    uploader = _mock_uploader()

    n = upload_pending_segments(root=segment_root, session_id=session_id, uploader=uploader)
    assert n == 0
    uploader.upload_segment_dir.assert_not_called()
