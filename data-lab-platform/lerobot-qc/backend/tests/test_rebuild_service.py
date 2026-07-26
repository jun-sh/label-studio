"""Tests for rebuild job bookkeeping."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import rebuild_service
from qc_store import QcStore


def test_start_rebuild_job_does_not_duplicate_job_id_kwarg(tmp_path: Path) -> None:
    state = MagicMock()
    state.dataset_root = tmp_path / "source"
    state.dataset_root.mkdir()
    store = MagicMock()
    store.manifest = {"rebuild_jobs": []}
    store.active_rebuild_job.return_value = None
    store.register_rebuild_job = MagicMock()
    delivery_root = tmp_path / "delivery"
    delivery_root.mkdir()

    with patch.object(rebuild_service.threading, "Thread") as thread_cls:
        thread_cls.return_value.start = MagicMock()
        job = rebuild_service.start_rebuild_job(
            state=state,
            store=store,
            delivery_root=delivery_root,
            batch_id="testbatch",
        )

    assert job["job_id"]
    assert job["status"] == "queued"
    stored = rebuild_service.get_job(job["job_id"])
    assert stored is not None
    assert stored["status"] == "queued"
    assert stored["batch_id"] == "testbatch"


def test_allocate_delivery_path_suffixes_on_collision(tmp_path: Path) -> None:
    delivery_root = tmp_path / "delivery"
    delivery_root.mkdir()
    existing = delivery_root / "pusht_v20260726"
    existing.mkdir()

    batch, output_root = rebuild_service.allocate_delivery_path(delivery_root, "pusht", "20260726")
    assert batch == "20260726_1"
    assert output_root == delivery_root / "pusht_v20260726_1"


def test_get_job_falls_back_to_sidecar(tmp_path: Path) -> None:
    dataset_root = tmp_path / "source"
    dataset_root.mkdir()
    store = QcStore.open(dataset_root, tmp_path / "sidecar", operator_id="tester")
    store.register_rebuild_job(
        {
            "job_id": "abc123",
            "status": "completed",
            "created_at": "2026-07-26T00:00:00+00:00",
            "batch_id": "batch-a",
            "output_root": str(tmp_path / "out"),
        }
    )

    job = rebuild_service.get_job("abc123", store=store)
    assert job is not None
    assert job["status"] == "completed"
    assert rebuild_service.get_job("abc123")["status"] == "completed"


def test_start_rebuild_job_rejects_concurrent_job(tmp_path: Path) -> None:
    state = MagicMock()
    state.dataset_root = tmp_path / "source"
    state.dataset_root.mkdir()
    store = MagicMock()
    store.active_rebuild_job.return_value = {"job_id": "running1", "status": "running"}
    store.register_rebuild_job = MagicMock()
    delivery_root = tmp_path / "delivery"
    delivery_root.mkdir()

    with pytest.raises(ValueError, match="already in progress"):
        rebuild_service.start_rebuild_job(
            state=state,
            store=store,
            delivery_root=delivery_root,
            batch_id="testbatch",
        )


def test_cleanup_incomplete_output_removes_partial_dir(tmp_path: Path) -> None:
    output_root = tmp_path / "partial"
    (output_root / "videos").mkdir(parents=True)
    rebuild_service._cleanup_incomplete_output(output_root)
    assert not output_root.exists()


def test_cleanup_incomplete_output_keeps_completed_dir(tmp_path: Path) -> None:
    output_root = tmp_path / "complete"
    (output_root / "meta").mkdir(parents=True)
    (output_root / "meta" / "info.json").write_text("{}", encoding="utf-8")
    rebuild_service._cleanup_incomplete_output(output_root)
    assert output_root.is_dir()


def test_reconcile_stale_jobs_marks_orphaned_running_failed(tmp_path: Path) -> None:
    dataset_root = tmp_path / "source"
    dataset_root.mkdir()
    store = QcStore.open(dataset_root, tmp_path / "sidecar", operator_id="tester")
    store.register_rebuild_job(
        {
            "job_id": "stale1",
            "status": "running",
            "created_at": "2026-07-26T00:00:00+00:00",
            "batch_id": "batch-a",
            "output_root": str(tmp_path / "out"),
        }
    )

    rebuild_service.reconcile_stale_jobs(store)
    job = store.get_rebuild_job("stale1")
    assert job is not None
    assert job["status"] == "failed"
    assert "restarted" in job["error"]
