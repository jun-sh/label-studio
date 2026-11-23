"""Interrupted rebuilds resume after a service restart instead of failing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import rebuild_service
from dataset_manager import load_local_dataset
from qc_store import QcStore, forget_open_stores
from test_minimal_rebuild import _prepare_store, minimal_dataset  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _fresh_state():
    forget_open_stores()
    rebuild_service._jobs.clear()
    yield
    forget_open_stores()
    rebuild_service._jobs.clear()


def _fake_reencode_factory(calls: list[Path], fail_on_call: int | None = None):
    def _fake(src: Path, dst: Path, ranges, fps, vcodec, pix_fmt) -> None:
        calls.append(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if fail_on_call is not None and len(calls) == fail_on_call:
            dst.write_bytes(b"half-written")  # what a kill mid-encode leaves behind
            raise KeyboardInterrupt("simulated service restart")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())

    return _fake


def _read_outputs(root: Path) -> dict[str, object]:
    return {
        "episodes": pd.read_parquet(root / "meta/episodes/chunk-000/file-000.parquet").to_json(),
        "data": pd.read_parquet(root / "data/chunk-000/file-000.parquet").to_json(),
        "tasks": pd.read_parquet(root / "meta/tasks.parquet").to_json(),
        "info": json.loads((root / "meta/info.json").read_text()),
        "videos": sorted(str(p.relative_to(root)) for p in (root / "videos").rglob("*") if p.is_file()),
    }


def test_interrupted_minimal_rebuild_resumes_to_identical_output(minimal_dataset, tmp_path: Path) -> None:
    dataset_root, sidecar_base = minimal_dataset
    store = _prepare_store(dataset_root, sidecar_base)
    state = load_local_dataset(str(dataset_root))
    quiet = patch.object(rebuild_service, "_sync_job")

    reference = tmp_path / "reference"
    calls: list[Path] = []
    with quiet, patch("video_delivery.keep_episodes_from_video", side_effect=_fake_reencode_factory(calls)):
        rebuild_service._rebuild_minimal_delivery_dataset(
            state=state, store=store, output_root=reference, batch_id="ref", job_id="ref"
        )
    assert len(calls) == 2  # both mixed video files are re-encoded

    interrupted = tmp_path / "interrupted"
    calls = []
    with quiet, patch(
        "video_delivery.keep_episodes_from_video", side_effect=_fake_reencode_factory(calls, fail_on_call=2)
    ), pytest.raises(KeyboardInterrupt):
        rebuild_service._rebuild_minimal_delivery_dataset(
            state=state, store=store, output_root=interrupted, batch_id="b", job_id="job1"
        )
    assert (interrupted / rebuild_service.JOB_MARKER).is_file()
    assert any(".partial" in p.name for p in (interrupted / "videos").rglob("*"))

    calls = []
    with quiet, patch("video_delivery.keep_episodes_from_video", side_effect=_fake_reencode_factory(calls)):
        result = rebuild_service._rebuild_minimal_delivery_dataset(
            state=state, store=store, output_root=interrupted, batch_id="b", job_id="job1", resume=True
        )
    assert len(calls) == 1  # only the file that was cut off is redone
    assert result["videos_resumed"] == 1
    assert not (interrupted / rebuild_service.JOB_MARKER).exists()
    assert not any(".partial" in p.name for p in (interrupted / "videos").rglob("*"))
    assert _read_outputs(interrupted) == _read_outputs(reference)


def test_resume_refuses_directory_owned_by_another_job(minimal_dataset, tmp_path: Path) -> None:
    dataset_root, sidecar_base = minimal_dataset
    store = _prepare_store(dataset_root, sidecar_base)
    state = load_local_dataset(str(dataset_root))
    out = tmp_path / "out"
    rebuild_service._write_marker(out, "someone-else")
    with patch.object(rebuild_service, "_sync_job"), pytest.raises(ValueError, match="not owned"):
        rebuild_service._rebuild_minimal_delivery_dataset(
            state=state, store=store, output_root=out, batch_id="b", job_id="job1", resume=True
        )


# ---- orchestration -------------------------------------------------------------------------


def _interrupted_job(tmp_path: Path, *, started_at: str | None, owned: bool, **job_extra) -> tuple[QcStore, dict, Path]:
    dataset_root = tmp_path / "ds"
    dataset_root.mkdir(exist_ok=True)
    sidecar = tmp_path / "sidecar"
    store = QcStore.open(dataset_root, sidecar)
    output_root = tmp_path / "delivery" / "ds_v1"
    if owned:
        rebuild_service._write_marker(output_root, "j1")
        (output_root / "videos").mkdir()
        (output_root / "videos" / "done.mp4").write_bytes(b"ok")
    job = {
        "job_id": "j1",
        "status": "running",
        "output_root": str(output_root),
        "batch_id": "v1",
        "rebuild_mode": "minimal",
        **({"started_at": started_at} if started_at else {}),
        **job_extra,
    }
    store.register_rebuild_job(job)
    return store, {"job": job, "dataset_root": str(dataset_root)}, sidecar


def _run_resume(item: dict, sidecar: Path):
    state = MagicMock()
    state.dataset_root = Path(item["dataset_root"])
    with patch.object(rebuild_service, "_launch_worker") as launch:
        rebuild_service._resume_one(item, sidecar, lambda _path: state)
    return launch


def test_resume_when_reviews_unchanged(tmp_path: Path) -> None:
    store, item, sidecar = _interrupted_job(tmp_path, started_at="2999-01-01T00:00:00+00:00", owned=True)
    launch = _run_resume(item, sidecar)
    kwargs = launch.call_args.kwargs
    assert kwargs["resume"] is True
    assert kwargs["started_at"] == "2999-01-01T00:00:00+00:00"
    assert (Path(item["job"]["output_root"]) / "videos" / "done.mp4").is_file()
    assert store.get_rebuild_job("j1")["resume_attempts"] == 1
    rebuild_service._unregister_running_dataset(Path(item["dataset_root"]), "j1")


def test_restart_from_scratch_when_reviews_changed(tmp_path: Path) -> None:
    store, item, sidecar = _interrupted_job(tmp_path, started_at="2000-01-01T00:00:00+00:00", owned=True)
    store.set_review(0, "rejected", reason="bad")  # newer than the job's snapshot
    launch = _run_resume(item, sidecar)
    kwargs = launch.call_args.kwargs
    assert kwargs["resume"] is False
    assert kwargs["started_at"] is None
    assert not Path(item["job"]["output_root"]).exists()
    rebuild_service._unregister_running_dataset(Path(item["dataset_root"]), "j1")


def test_gives_up_after_max_attempts_and_cleans_output(tmp_path: Path) -> None:
    store, item, sidecar = _interrupted_job(
        tmp_path,
        started_at="2999-01-01T00:00:00+00:00",
        owned=True,
        resume_attempts=rebuild_service.MAX_RESUME_ATTEMPTS,
    )
    launch = _run_resume(item, sidecar)
    launch.assert_not_called()
    job = store.get_rebuild_job("j1")
    assert job["status"] == "failed" and "gave up" in job["error"]
    assert not Path(item["job"]["output_root"]).exists()


def test_unowned_output_is_left_untouched(tmp_path: Path) -> None:
    store, item, sidecar = _interrupted_job(tmp_path, started_at="2999-01-01T00:00:00+00:00", owned=False)
    out = Path(item["job"]["output_root"])
    out.mkdir(parents=True)
    (out / "precious.txt").write_text("x")
    launch = _run_resume(item, sidecar)
    launch.assert_not_called()
    assert store.get_rebuild_job("j1")["status"] == "failed"
    assert (out / "precious.txt").is_file()


def test_legacy_job_is_failed_not_resumed(tmp_path: Path) -> None:
    store, item, sidecar = _interrupted_job(
        tmp_path, started_at="2999-01-01T00:00:00+00:00", owned=True, rebuild_mode="legacy"
    )
    launch = _run_resume(item, sidecar)
    launch.assert_not_called()
    assert "legacy" in store.get_rebuild_job("j1")["error"]


def test_collect_reserves_jobs_so_dataset_load_does_not_fail_them(tmp_path: Path) -> None:
    store, item, sidecar = _interrupted_job(tmp_path, started_at="2999-01-01T00:00:00+00:00", owned=True)
    found = rebuild_service.collect_interrupted_jobs(sidecar)
    assert [f["job"]["job_id"] for f in found] == ["j1"]
    try:
        rebuild_service.reconcile_stale_jobs(store)
        assert store.get_rebuild_job("j1")["status"] == "running"
    finally:
        rebuild_service._resume_pending.discard("j1")
    rebuild_service.reconcile_stale_jobs(store)
    assert store.get_rebuild_job("j1")["status"] == "failed"


# ---- shared store (rebuild progress must not overwrite reviews) -----------------------------


def test_store_is_shared_and_progress_updates_keep_new_reviews(tmp_path: Path) -> None:
    dataset_root = tmp_path / "ds"
    dataset_root.mkdir()
    rebuild_store = QcStore.open(dataset_root, tmp_path / "sidecar")
    rebuild_store.register_rebuild_job({"job_id": "j", "status": "running"})
    session_store = QcStore.open(dataset_root, tmp_path / "sidecar", operator_id="reviewer")
    assert session_store is rebuild_store

    session_store.set_review(5, "rejected", reason="blurry")
    rebuild_store.update_rebuild_job("j", {"progress": "videos 3/10"})

    on_disk = json.loads(rebuild_store.manifest_path.read_text())
    assert on_disk["reviews"]["5"]["status"] == "rejected"
    assert on_disk["rebuild_jobs"][0]["progress"] == "videos 3/10"
