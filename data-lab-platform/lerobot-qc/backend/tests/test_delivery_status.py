"""Tests for delivery freshness reported alongside the review summary."""

from __future__ import annotations

from pathlib import Path

from qc_store import QcStore


def _store(tmp_path: Path) -> QcStore:
    root = tmp_path / "ds"
    root.mkdir()
    return QcStore.open(root, tmp_path / "sidecar", operator_id="tester")


def _complete_job(store: QcStore, output_root: Path, started_at: str) -> None:
    store.manifest.setdefault("rebuild_jobs", []).append(
        {
            "job_id": started_at,
            "status": "completed",
            "created_at": started_at,
            "started_at": started_at,
            "finished_at": started_at,
            "output_root": str(output_root),
            "kept_episodes": 3,
        }
    )


def test_never_built(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.delivery_status()["state"] == "never_built"
    assert store.review_summary(total_episodes=2)["delivery"]["state"] == "never_built"


def test_current_then_stale_after_review(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_review(0, "approved")
    out = tmp_path / "delivery" / "ds_v1"
    out.mkdir(parents=True)
    _complete_job(store, out, "2999-01-01T00:00:00+00:00")
    status = store.delivery_status()
    assert status["state"] == "current"
    assert status["output_root"] == str(out)
    assert status["kept_episodes"] == 3

    store.manifest["rebuild_jobs"][-1]["started_at"] = "2000-01-01T00:00:00+00:00"
    store.set_review(1, "rejected", reason="bad")
    assert store.delivery_status()["state"] == "stale"


def test_instruction_edit_marks_stale(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = tmp_path / "delivery" / "ds_v1"
    out.mkdir(parents=True)
    _complete_job(store, out, "2000-01-01T00:00:00+00:00")
    assert store.delivery_status()["state"] == "current"
    store.set_instruction_override(0, "new text", original_instruction="old")
    assert store.delivery_status()["state"] == "stale"


def test_missing_output_dir(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _complete_job(store, tmp_path / "gone", "2999-01-01T00:00:00+00:00")
    assert store.delivery_status()["state"] == "missing"


def test_legacy_manifest_falls_back_to_review_times(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.manifest["reviews"] = {"0": {"status": "approved", "updated_at": "2026-09-04T06:47:26+00:00"}}
    store.manifest.pop("qc_updated_at", None)
    out = tmp_path / "out"
    out.mkdir()
    _complete_job(store, out, "2026-07-30T00:00:00+00:00")
    assert store.delivery_status()["state"] == "stale"
