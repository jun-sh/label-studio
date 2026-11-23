"""Tests for rebuild job bookkeeping."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import rebuild_service
from qc_store import QcStore


def test_start_rebuild_job_does_not_duplicate_job_id_kwarg(tmp_path: Path) -> None:
    state = MagicMock()
    state.dataset_root = tmp_path / "source"
    state.dataset_root.mkdir()
    state.episodes_df = pd.DataFrame({"episode_index": [0, 1]})
    store = MagicMock()
    store.manifest = {"rebuild_jobs": []}
    store.active_rebuild_job.return_value = None
    store.removed_episode_indices.return_value = {1}
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


def _job_fixture(tmp_path: Path, removed: set[int]) -> tuple[MagicMock, MagicMock, Path]:
    state = MagicMock()
    state.dataset_root = tmp_path / "source"
    state.dataset_root.mkdir()
    state.episodes_df = pd.DataFrame({"episode_index": [0, 1, 2]})
    store = MagicMock()
    store.manifest = {"rebuild_jobs": []}
    store.active_rebuild_job.return_value = None
    store.removed_episode_indices.return_value = removed
    delivery_root = tmp_path / "delivery"
    delivery_root.mkdir()
    return state, store, delivery_root


def test_start_rebuild_job_rejects_when_all_episodes_removed(tmp_path: Path) -> None:
    state, store, delivery_root = _job_fixture(tmp_path, removed={0, 1, 2})
    with pytest.raises(ValueError, match="nothing to deliver"):
        rebuild_service.start_rebuild_job(state=state, store=store, delivery_root=delivery_root, batch_id="b")
    assert list(delivery_root.iterdir()) == []
    store.register_rebuild_job.assert_not_called()


def test_failed_rebuild_removes_output_even_with_info_json(tmp_path: Path) -> None:
    state, store, delivery_root = _job_fixture(tmp_path, removed=set())

    def _fail_after_copy(*, output_root: Path, job_id: str, **_kwargs) -> None:
        rebuild_service._write_marker(output_root, job_id)
        (output_root / "meta").mkdir(parents=True)
        (output_root / "meta" / "info.json").write_text("{}", encoding="utf-8")
        raise RuntimeError("boom")

    with patch.object(rebuild_service, "rebuild_delivery_dataset", side_effect=_fail_after_copy), patch.object(
        rebuild_service, "_sync_job"
    ):
        job = rebuild_service.start_rebuild_job(state=state, store=store, delivery_root=delivery_root, batch_id="b")
        for thread in [t for t in rebuild_service.threading.enumerate() if t.name == f"qc-rebuild-{job['job_id']}"]:
            thread.join(timeout=10)
    assert not Path(job["output_root"]).exists()


def test_cleanup_failed_output_only_removes_dirs_owned_by_the_job(tmp_path: Path) -> None:
    output_root = tmp_path / "existing"
    (output_root / "meta").mkdir(parents=True)
    rebuild_service._cleanup_failed_output(output_root, "job-a")  # no marker: someone else's
    assert output_root.is_dir()
    rebuild_service._write_marker(output_root, "job-b")
    rebuild_service._cleanup_failed_output(output_root, "job-a")  # other job's marker
    assert output_root.is_dir()
    rebuild_service._cleanup_failed_output(output_root, "job-b")
    assert not output_root.exists()


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


def test_normalize_episode_tasks_mixed_types_write_parquet() -> None:
    import numpy as np
    import pandas as pd
    import pyarrow as pa

    ndarray_task = np.array(["pick the cube"], dtype=object)
    rows = [
        {"episode_index": 0, "tasks": rebuild_service._normalize_episode_tasks("override text")},
        {"episode_index": 1, "tasks": rebuild_service._normalize_episode_tasks(ndarray_task)},
    ]
    table = pa.Table.from_pandas(pd.DataFrame(rows), preserve_index=False)
    assert table.num_rows == 2
