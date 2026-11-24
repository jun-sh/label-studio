"""Tests for the cross-package QC overview."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import overview_service
from qc_store import dataset_sidecar_id


def _write_sidecar(base: Path, dataset_root: Path, manifest: dict, name: str | None = None) -> None:
    folder = base / (name or dataset_sidecar_id(dataset_root))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "qc_manifest.json").write_text(
        json.dumps({"dataset_root": str(dataset_root), **manifest}), encoding="utf-8"
    )


def _reviews(**by_status: list[int]) -> dict:
    return {
        str(ep): {"status": status, "updated_at": "2026-09-01T00:00:00+00:00"}
        for status, eps in by_status.items()
        for ep in eps
    }


def _completed_job(output_root: Path, started_at: str = "2026-10-01T00:00:00+00:00") -> dict:
    return {
        "job_id": "j1",
        "status": "completed",
        "started_at": started_at,
        "finished_at": started_at,
        "output_root": str(output_root),
        "kept_episodes": 2,
    }


def _collection(tmp_path: Path, ids: list[str], episodes: int = 3) -> dict:
    root = tmp_path / "BookDuo_2" / "coll"
    return {
        "id": "coll",
        "title": "Coll",
        "packages": [{"id": pid, "local_path": str(root / pid), "episodes": episodes, "loadable": True} for pid in ids],
    }


def test_stages_and_totals(tmp_path: Path) -> None:
    sidecar = tmp_path / "sidecar"
    collection = _collection(tmp_path, ["p_new", "p_review", "p_susp", "p_rebuild", "p_done", "p_stale", "p_gone", "p_all_removed", "p_running"])
    paths = {p["id"]: Path(p["local_path"]) for p in collection["packages"]}
    out = tmp_path / "delivery" / "done"
    out.mkdir(parents=True)

    _write_sidecar(sidecar, paths["p_review"], {"reviews": _reviews(approved=[0])})
    _write_sidecar(sidecar, paths["p_susp"], {"reviews": _reviews(approved=[0, 1], suspicious=[2])})
    _write_sidecar(sidecar, paths["p_rebuild"], {"reviews": _reviews(approved=[0, 1], rejected=[2])})
    _write_sidecar(
        sidecar,
        paths["p_done"],
        {"reviews": _reviews(approved=[0, 1], rejected=[2]), "rebuild_jobs": [_completed_job(out)]},
    )
    _write_sidecar(
        sidecar,
        paths["p_stale"],
        {
            "reviews": _reviews(approved=[0, 1, 2]),
            "qc_updated_at": "2026-11-01T00:00:00+00:00",
            "rebuild_jobs": [_completed_job(out)],
        },
    )
    _write_sidecar(
        sidecar,
        paths["p_gone"],
        {"reviews": _reviews(approved=[0, 1, 2]), "rebuild_jobs": [_completed_job(tmp_path / "missing")]},
    )
    _write_sidecar(sidecar, paths["p_all_removed"], {"reviews": _reviews(rejected=[0, 1, 2])})
    _write_sidecar(
        sidecar,
        paths["p_running"],
        {"reviews": _reviews(approved=[0, 1, 2]), "rebuild_jobs": [{"job_id": "live", "status": "running"}]},
    )

    with patch.object(overview_service, "get_collection", return_value=collection):
        result = overview_service.build_overview("coll", sidecar, running_jobs={"live"})

    rows = {row["id"]: row for row in result["packages"]}
    assert rows["p_new"]["stage"] == "not_started"
    assert rows["p_new"]["pending"] == 3
    assert rows["p_review"]["stage"] == "in_review"
    assert rows["p_review"]["reviewed"] == 1
    assert rows["p_susp"]["stage"] == "suspicious"
    assert rows["p_rebuild"]["stage"] == "needs_rebuild"
    assert rows["p_rebuild"]["delivery"]["state"] == "never_built"
    assert rows["p_done"]["stage"] == "delivered"
    assert rows["p_stale"]["stage"] == "needs_rebuild"
    assert rows["p_stale"]["delivery"]["state"] == "stale"
    assert rows["p_gone"]["delivery"]["state"] == "missing"
    assert rows["p_all_removed"]["stage"] == "all_removed"
    assert rows["p_running"]["stage"] == "rebuilding"
    assert result["stage_counts"]["needs_rebuild"] == 3
    assert result["totals"]["episodes"] == 27
    assert result["totals"]["suspicious"] == 1


def test_stale_running_job_after_restart_is_not_rebuilding(tmp_path: Path) -> None:
    sidecar = tmp_path / "sidecar"
    collection = _collection(tmp_path, ["p"])
    _write_sidecar(
        sidecar,
        Path(collection["packages"][0]["local_path"]),
        {"reviews": _reviews(approved=[0, 1, 2]), "rebuild_jobs": [{"job_id": "dead", "status": "running"}]},
    )
    with patch.object(overview_service, "get_collection", return_value=collection):
        row = overview_service.build_overview("coll", sidecar, running_jobs=set())["packages"][0]
    assert row["stage"] == "needs_rebuild"
    assert row["rebuild_job"] is None


def test_sidecar_matched_after_dataset_moved_and_richest_duplicate_wins(tmp_path: Path) -> None:
    sidecar = tmp_path / "sidecar"
    collection = _collection(tmp_path, ["681448"])
    old_root = tmp_path / "BookDuo_2" / "old_location" / "coll" / "681448"
    _write_sidecar(sidecar, old_root, {"reviews": _reviews(approved=[0])}, name="dup_small")
    _write_sidecar(sidecar, old_root, {"reviews": _reviews(approved=[0, 1, 2])}, name="dup_big")
    with patch.object(overview_service, "get_collection", return_value=collection):
        row = overview_service.build_overview("coll", sidecar, running_jobs=set())["packages"][0]
    assert row["approved"] == 3


def test_overview_does_not_create_sidecars(tmp_path: Path) -> None:
    sidecar = tmp_path / "sidecar"
    sidecar.mkdir()
    collection = _collection(tmp_path, ["a", "b"])
    with patch.object(overview_service, "get_collection", return_value=collection):
        overview_service.build_overview("coll", sidecar, running_jobs=set())
    assert list(sidecar.iterdir()) == []


def test_overview_api_and_page(tmp_path: Path) -> None:
    import app as qc_app

    collection = _collection(tmp_path, ["a"])
    client = TestClient(qc_app.app)
    with patch.object(overview_service, "get_collection", return_value=collection), patch.object(
        qc_app, "SIDECAR_ROOT", tmp_path / "sidecar"
    ):
        response = client.get("/api/qc/overview", params={"collection": "coll"})
    assert response.status_code == 200
    assert response.json()["packages"][0]["stage"] == "not_started"

    with patch.object(overview_service, "get_collection", side_effect=KeyError("nope")):
        assert client.get("/api/qc/overview", params={"collection": "nope"}).status_code == 404

    page = client.get("/overview")
    assert page.status_code == 200
    assert "overview.js?v=" in page.text
