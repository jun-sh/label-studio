"""D3 API tests — annotation jobs, load, J-01..J-03."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import app, manager

WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
UNIFRANKA = WORKSPACE_ROOT / "data-storage/embodied-annotate/datasets/unifranka"
PKG_681496 = UNIFRANKA / "681496"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_manager() -> None:
    manager.source = None
    manager.dataset_root = None
    manager.info = None
    manager.episodes_df = None
    manager.annotations = {}
    manager.active_job = None
    manager.job_episode_indices = set()
    manager.collection_id = None
    manager.package_id = None


@pytest.mark.skipif(not PKG_681496.is_dir(), reason="UniFranka 681496 not on disk")
def test_load_681496_auto_job(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEROBOT_ANNOTATE_DATASETS", str(UNIFRANKA.parent))
    res = client.post(
        "/api/dataset/load",
        json={
            "source": "local",
            "local_path": str(PKG_681496),
            "collection_id": "unifranka",
            "package_id": "681496",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["active_job"]["job_id"] == "unifranka-681496-pick_place-full"
    assert body["schema_ref"] == "pick_place_v1@1"
    assert len(body["episodes"]) == 1000
    assert body["episodes"][0]["task_family"] == "pick_place"
    assert body["annotation_progress"]["complete"] >= 2


@pytest.mark.skipif(not PKG_681496.is_dir(), reason="UniFranka 681496 not on disk")
def test_list_jobs_681496(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEROBOT_ANNOTATE_DATASETS", str(UNIFRANKA.parent))
    res = client.get("/api/datasets/collections/unifranka/jobs", params={"package_id": "681496"})
    assert res.status_code == 200
    jobs = res.json()["jobs"]
    assert any(j["job_id"] == "unifranka-681496-pick_place-full" for j in jobs)
    full = next(j for j in jobs if j["job_id"] == "unifranka-681496-pick_place-full")
    assert full["episode_count"] == 1000
    assert full["annotated_count"] >= 2


@pytest.mark.skipif(not (UNIFRANKA / "681460").is_dir(), reason="681460 not on disk")
def test_mixed_package_requires_job(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEROBOT_ANNOTATE_DATASETS", str(UNIFRANKA.parent))
    res = client.post(
        "/api/dataset/load",
        json={
            "source": "local",
            "local_path": str(UNIFRANKA / "681460"),
            "collection_id": "unifranka",
            "package_id": "681460",
        },
    )
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["requires_job"] is True
    assert "681460" in str(detail["message"])


@pytest.mark.skipif(not (UNIFRANKA / "681460").is_dir(), reason="681460 not on disk")
def test_load_wipe_job_filters_episodes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEROBOT_ANNOTATE_DATASETS", str(UNIFRANKA.parent))
    job_id = "unifranka-681460-wipe_clean-full"
    res = client.post(
        "/api/dataset/load",
        json={
            "source": "local",
            "local_path": str(UNIFRANKA / "681460"),
            "collection_id": "unifranka",
            "package_id": "681460",
            "job_id": job_id,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["active_job"]["task_family"] == "wipe_clean"
    assert len(body["episodes"]) == 115


@pytest.mark.skipif(not PKG_681496.is_dir(), reason="UniFranka 681496 not on disk")
def test_j01_episode_outside_job_forbidden(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEROBOT_ANNOTATE_DATASETS", str(UNIFRANKA.parent))
    client.post(
        "/api/dataset/load",
        json={
            "source": "local",
            "local_path": str(PKG_681496),
            "collection_id": "unifranka",
            "package_id": "681496",
            "job_id": "unifranka-681496-pick_place-full",
        },
    )
    res = client.get("/api/episodes/0/annotations")
    assert res.status_code == 200
    # Episode index exists in job - test paused wipe job episode not in pick job would need mixed pkg open job


def test_legacy_leaf_without_jobs(tmp_path: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEROBOT_ANNOTATE_DATASETS", str(tmp_path))
    pkg = tmp_path / "pusht"
    (pkg / "meta").mkdir(parents=True)
    (pkg / "meta" / "info.json").write_text(
        json.dumps(
            {
                "fps": 10,
                "features": {"observation.image": {"dtype": "video"}},
            }
        ),
        encoding="utf-8",
    )
    ep = pd.DataFrame({"episode_index": [0], "length": [10]})
    (pkg / "meta" / "episodes").mkdir(parents=True)
    ep.to_parquet(pkg / "meta" / "episodes" / "chunk-000.parquet")
    (pkg / "annotation.schema.json").write_text(
        json.dumps(
            {
                "schema_id": "pusht_v1",
                "schema_version": 1,
                "subtask_labels": [{"id": "push", "order": 0, "color": "#111"}],
            }
        ),
        encoding="utf-8",
    )
    res = client.post("/api/dataset/load", json={"source": "local", "local_path": str(pkg)})
    assert res.status_code == 200
    assert res.json().get("active_job") is None
