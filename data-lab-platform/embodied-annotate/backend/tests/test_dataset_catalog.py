"""Tests for embodied-annotate dataset catalog API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app


@pytest.fixture()
def catalog_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LEROBOT_ANNOTATE_DATASETS", str(tmp_path))

    leaf = tmp_path / "pusht"
    (leaf / "meta").mkdir(parents=True)
    (leaf / "meta" / "info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "robot_type": "unknown",
                "total_episodes": 2,
                "total_frames": 20,
                "fps": 10,
                "features": {
                    "observation.image": {"dtype": "video", "shape": [96, 96, 3]},
                },
            }
        ),
        encoding="utf-8",
    )

    collection = tmp_path / "limx_box_transport"
    collection.mkdir()
    (collection / "collection.manifest.json").write_text(
        json.dumps(
            {
                "collection_id": "limx_box_transport",
                "title": "limx_box_transport",
                "annotation_schema": {
                    "schema_id": "box_transport_v1",
                    "schema_version": 1,
                    "subtask_labels": [{"id": "reach", "order": 0, "color": "#000000"}],
                    "episode_fields": [{"id": "box_cycle", "type": "int", "required": True}],
                },
                "packages": [
                    {"id": "431132", "display_name": "头摄 + 全身位姿", "local_path": "431132"},
                    {"id": "431133", "display_name": "单目相机", "local_path": "431133"},
                ],
            }
        ),
        encoding="utf-8",
    )
    for pkg_id in ("431132", "431133"):
        pkg = collection / pkg_id
        (pkg / "meta").mkdir(parents=True)
        (pkg / "meta" / "info.json").write_text(
            json.dumps(
                {
                    "codebase_version": "v3.0",
                    "robot_type": "LimX_Oli",
                    "total_episodes": 10,
                    "total_frames": 100,
                    "fps": 20,
                    "features": {
                        f"observation.images.camera_{pkg_id}": {"dtype": "video", "shape": [240, 320, 3]},
                    },
                }
            ),
            encoding="utf-8",
        )

    return tmp_path


def test_list_collections(catalog_root: Path) -> None:
    client = TestClient(app)
    res = client.get("/api/datasets/collections")
    assert res.status_code == 200
    body = res.json()
    assert body["datasets_root"] == str(catalog_root.resolve())
    ids = {c["id"] for c in body["collections"]}
    assert "pusht" in ids
    assert "limx_box_transport" in ids


def test_get_collection_detail(catalog_root: Path) -> None:
    client = TestClient(app)
    res = client.get("/api/datasets/collections/limx_box_transport")
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "limx_box_transport"
    assert body["annotation_schema"]["schema_id"] == "box_transport_v1"
    assert len(body["packages"]) == 2
    pkg = body["packages"][0]
    assert pkg["annotation_schema"]["schema_id"] == "box_transport_v1"
    assert any(f["id"] == "box_cycle" for f in pkg["annotation_schema"]["episode_fields"])
    first_pkg = body["packages"][0]
    assert first_pkg["id"] == "431132"
    assert first_pkg["loadable"] is True
    assert str(first_pkg["local_path"]).endswith("limx_box_transport/431132")


def test_get_collection_missing(catalog_root: Path) -> None:
    client = TestClient(app)
    res = client.get("/api/datasets/collections/does-not-exist")
    assert res.status_code == 404
