"""Tests for annotation schema resolution and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from annotation_schema import (
    filter_episode_fields,
    resolve_annotation_schema,
    schema_ref,
    validate_episode_fields,
)


def test_default_schema_for_unknown_dataset(tmp_path: Path) -> None:
    root = tmp_path / "unknown"
    (root / "meta").mkdir(parents=True)
    (root / "meta" / "info.json").write_text("{}", encoding="utf-8")
    schema = resolve_annotation_schema(root)
    assert schema["schema_id"] == "default_manipulation_v1"
    assert len(schema["subtask_labels"]) == 8
    assert schema["episode_fields"] == []


def test_manifest_collection_schema(tmp_path: Path) -> None:
    collection = tmp_path / "limx_box_transport"
    pkg = collection / "431132"
    (pkg / "meta").mkdir(parents=True)
    (pkg / "meta" / "info.json").write_text("{}", encoding="utf-8")
    (collection / "collection.manifest.json").write_text(
        json.dumps(
            {
                "collection_id": "limx_box_transport",
                "annotation_schema": {
                    "schema_id": "box_transport_v1",
                    "schema_version": 1,
                    "subtask_labels": [{"id": "reach", "order": 0, "color": "#000000"}],
                    "episode_fields": [{"id": "box_cycle", "type": "int", "required": True, "min": 0}],
                },
                "packages": [{"id": "431132"}],
            }
        ),
        encoding="utf-8",
    )
    schema = resolve_annotation_schema(pkg)
    assert schema["schema_id"] == "box_transport_v1"
    assert schema["subtask_labels"][0]["id"] == "reach"
    assert schema["episode_fields"][0]["id"] == "box_cycle"
    assert schema_ref(schema) == "box_transport_v1@1"


def test_leaf_annotation_schema_file(tmp_path: Path) -> None:
    root = tmp_path / "pusht"
    (root / "meta").mkdir(parents=True)
    (root / "meta" / "info.json").write_text("{}", encoding="utf-8")
    (root / "annotation.schema.json").write_text(
        json.dumps(
            {
                "schema_id": "pusht_v1",
                "schema_version": 1,
                "subtask_labels": [{"id": "push", "order": 0, "color": "#111111"}],
                "episode_fields": [],
            }
        ),
        encoding="utf-8",
    )
    schema = resolve_annotation_schema(root)
    assert schema["schema_id"] == "pusht_v1"
    assert schema["subtask_labels"][0]["id"] == "push"


def test_validate_episode_fields_required() -> None:
    schema = {
        "episode_fields": [{"id": "box_cycle", "type": "int", "required": True, "min": 0}],
    }
    validate_episode_fields(schema, {"box_cycle": 0})
    with pytest.raises(HTTPException):
        validate_episode_fields(schema, {})
    with pytest.raises(HTTPException):
        validate_episode_fields(schema, {"box_cycle": -1})


def test_filter_episode_fields_drops_legacy_notes() -> None:
    schema = {
        "episode_fields": [{"id": "box_cycle", "type": "int", "required": True}],
    }
    filtered = filter_episode_fields(schema, {"box_cycle": 2, "notes": "随便写的中文"})
    assert filtered == {"box_cycle": 2}
