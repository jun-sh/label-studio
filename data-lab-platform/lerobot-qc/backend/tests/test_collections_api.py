"""Tests for GET /api/datasets/collections."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module

COLLECTIONS = [{"id": "coll", "title": "Coll", "packages": [{"id": "p1"}]}]


def test_storage_status_included_by_default() -> None:
    with patch.object(app_module, "list_collections_cached", return_value=COLLECTIONS), patch.object(
        app_module, "get_storage_status", return_value={"volumes": []}
    ) as storage:
        body = TestClient(app_module.app).get("/api/datasets/collections").json()
    storage.assert_called_once()
    assert body["storage"] == {"volumes": []}
    assert body["collections"][0]["package_count"] == 1


def test_include_storage_false_skips_storage_probe() -> None:
    with patch.object(app_module, "list_collections_cached", return_value=COLLECTIONS), patch.object(
        app_module, "get_storage_status"
    ) as storage:
        body = TestClient(app_module.app).get("/api/datasets/collections", params={"include_storage": "false"}).json()
    storage.assert_not_called()
    assert body["storage"] is None
    assert [c["id"] for c in body["collections"]] == ["coll"]
