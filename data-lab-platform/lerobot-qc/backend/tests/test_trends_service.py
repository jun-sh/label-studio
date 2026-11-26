"""Tests for the daily QC trends used by the Datalab overview page."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from qc_store import dataset_sidecar_id
from trends_service import build_daily_trends

NOW = datetime(2026, 11, 26, 3, 0, tzinfo=timezone.utc)  # 11:00 on 11-26 in UTC+8


def _write_sidecar(base: Path, dataset_root: Path, manifest: dict) -> None:
    folder = base / dataset_sidecar_id(dataset_root)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "qc_manifest.json").write_text(
        json.dumps({"dataset_root": str(dataset_root), **manifest}), encoding="utf-8"
    )


def _row(result: dict, day: str) -> dict:
    return next(row for row in result["series"] if row["date"] == day)


def test_reviews_and_deliveries_bucketed_by_local_day(tmp_path: Path) -> None:
    _write_sidecar(
        tmp_path,
        tmp_path / "BookDuo_2" / "coll" / "pkg1",
        {
            "reviews": {
                "0": {"status": "approved", "updated_at": "2026-11-25T02:00:00+00:00"},
                "1": {"status": "rejected", "updated_at": "2026-11-25T02:30:00+00:00"},
                # 17:00 UTC on 11-25 is already 11-26 in UTC+8.
                "2": {"status": "suspicious", "updated_at": "2026-11-25T17:00:00+00:00"},
                "3": {"status": "pending", "updated_at": "2026-11-25T02:00:00+00:00"},
                "4": {"status": "approved", "updated_at": "2026-10-01T00:00:00+00:00"},  # out of range
            },
            "rebuild_jobs": [
                {"job_id": "a", "status": "completed", "finished_at": "2026-11-24T05:00:00+00:00", "kept_episodes": 115},
                {"job_id": "b", "status": "failed", "finished_at": "2026-11-24T06:00:00+00:00", "kept_episodes": 9},
                {"job_id": "c", "status": "running", "kept_episodes": 3},
            ],
        },
    )

    result = build_daily_trends(tmp_path, days=7, tz_offset_minutes=480, now=NOW)

    assert result["start"] == "2026-11-20" and result["end"] == "2026-11-26"
    assert [row["date"] for row in result["series"]][0] == "2026-11-20"
    assert len(result["series"]) == 7
    day25 = _row(result, "2026-11-25")
    assert (day25["reviewed"], day25["approved"], day25["rejected"]) == (2, 1, 1)
    day26 = _row(result, "2026-11-26")
    assert (day26["reviewed"], day26["suspicious"]) == (1, 1)
    day24 = _row(result, "2026-11-24")
    assert (day24["deliveries"], day24["delivered_episodes"]) == (1, 115)
    assert result["totals"]["reviewed"] == 3
    assert result["totals"]["deliveries"] == 1


def test_same_day_in_utc_when_no_offset(tmp_path: Path) -> None:
    _write_sidecar(
        tmp_path,
        tmp_path / "coll" / "pkg1",
        {"reviews": {"0": {"status": "approved", "updated_at": "2026-11-25T17:00:00Z"}}},
    )
    result = build_daily_trends(tmp_path, days=3, tz_offset_minutes=0, now=NOW)
    assert _row(result, "2026-11-25")["reviewed"] == 1
    assert _row(result, "2026-11-26")["reviewed"] == 0


def test_duplicate_sidecars_counted_once(tmp_path: Path) -> None:
    root = tmp_path / "BookDuo_2" / "coll" / "pkg1"
    reviews = {"0": {"status": "approved", "updated_at": "2026-11-25T02:00:00+00:00"}}
    _write_sidecar(tmp_path, root, {"reviews": reviews})
    # Same package (same parent/name) mounted under another prefix -> same canonical key.
    other = tmp_path / "other" / "BookDuo_2" / "coll" / "pkg1"
    (tmp_path / "dup").mkdir()
    (tmp_path / "dup" / "qc_manifest.json").write_text(
        json.dumps({"dataset_root": str(other), "reviews": reviews}), encoding="utf-8"
    )
    result = build_daily_trends(tmp_path, days=2, tz_offset_minutes=480, now=NOW)
    assert result["totals"]["reviewed"] == 1


def test_bad_values_are_ignored_and_days_clamped(tmp_path: Path) -> None:
    _write_sidecar(
        tmp_path,
        tmp_path / "coll" / "pkg1",
        {
            "reviews": {"0": {"status": "approved", "updated_at": "not a date"}, "1": "junk"},
            "rebuild_jobs": {"x": {"status": "completed", "finished_at": None}},
        },
    )
    result = build_daily_trends(tmp_path, days=1000, now=NOW)
    assert result["days"] == 180
    assert result["totals"]["reviewed"] == 0 and result["totals"]["deliveries"] == 0


def test_trends_endpoint(tmp_path: Path) -> None:
    import app as app_module

    _write_sidecar(
        tmp_path,
        tmp_path / "coll" / "pkg1",
        {"reviews": {"0": {"status": "approved", "updated_at": datetime.now(timezone.utc).isoformat()}}},
    )
    with patch.object(app_module, "SIDECAR_ROOT", tmp_path):
        client = TestClient(app_module.app)
        response = client.get("/api/qc/trends", params={"days": 7, "tz_offset_minutes": 480})
        assert response.status_code == 200
        body = response.json()
        assert body["days"] == 7 and len(body["series"]) == 7
        assert body["totals"]["reviewed"] == 1
        assert client.get("/api/qc/trends", params={"days": 0}).status_code == 422
        assert client.get("/api/qc/trends", params={"tz_offset_minutes": 2000}).status_code == 422
