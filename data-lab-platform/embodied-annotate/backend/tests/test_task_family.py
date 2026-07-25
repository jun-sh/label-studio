"""Tests for task_family resolution and per-episode schema routing (D1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from annotation_job import AnnotationJob
from task_family import (
    JobFamilyMismatchError,
    TaskFamilyUnresolvedError,
    build_task_text_lookup,
    default_schema_registry_dir,
    is_single_family_package,
    load_schema_by_id,
    load_task_family_map,
    resolve_schema_for_episode,
    resolve_task_family,
    schema_id_from_ref,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
UNIFRANKA_COLLECTION = WORKSPACE_ROOT / "data-storage/embodied-annotate/datasets/unifranka"
UNIFRANKA_MAP = UNIFRANKA_COLLECTION / "task-family-map.json"


@pytest.fixture()
def mini_family_map() -> dict:
    return {
        "collection_id": "unifranka",
        "schema_by_family": {
            "pick_place": "pick_place_v1",
            "wipe_clean": "wipe_clean_v1",
        },
        "packages": {
            "681496": {
                "dominant_family": "pick_place",
                "task_families": {"pick_place": 1000},
            },
            "681460": {
                "dominant_family": "pick_place",
                "task_families": {"pick_place": 871, "wipe_clean": 115},
            },
        },
        "tasks": [
            {
                "task_text": "Pick up an egg from the egg tray and place it on the plate.",
                "task_family": "pick_place",
                "schema_ref": "pick_place_v1@1",
                "packages": ["681496"],
            },
            {
                "task_text": "Clean the table with a broom.",
                "task_family": "wipe_clean",
                "schema_ref": "wipe_clean_v1@1",
                "packages": ["681460"],
            },
        ],
    }


def test_schema_id_from_ref() -> None:
    assert schema_id_from_ref("wipe_clean_v1@1") == "wipe_clean_v1"


def test_load_schema_by_id_pick_place() -> None:
    schema = load_schema_by_id("pick_place_v1", default_schema_registry_dir())
    assert schema["schema_id"] == "pick_place_v1"
    assert any(lbl["id"] == "pre_grasp" for lbl in schema["subtask_labels"])


def test_load_schema_by_id_wipe_clean_no_lift() -> None:
    schema = load_schema_by_id("wipe_clean_v1", default_schema_registry_dir())
    label_ids = {lbl["id"] for lbl in schema["subtask_labels"]}
    assert "wipe" in label_ids
    assert "lift" not in label_ids
    assert "transport" not in label_ids


def test_resolve_task_family_pick_place(mini_family_map: dict) -> None:
    family, ref = resolve_task_family(
        collection_id="unifranka",
        package_id="681496",
        episode_index=0,
        task_text="Pick up an egg from the egg tray and place it on the plate.",
        task_index=0,
        family_map=mini_family_map,
    )
    assert family == "pick_place"
    assert ref == "pick_place_v1@1"


def test_resolve_task_family_wipe_clean(mini_family_map: dict) -> None:
    family, ref = resolve_task_family(
        collection_id="unifranka",
        package_id="681460",
        episode_index=562,
        task_text="Clean the table with a broom.",
        task_index=None,
        family_map=mini_family_map,
    )
    assert family == "wipe_clean"
    assert ref == "wipe_clean_v1@1"


def test_resolve_task_family_wrong_package_raises(mini_family_map: dict) -> None:
    with pytest.raises(TaskFamilyUnresolvedError, match="not registered"):
        resolve_task_family(
            collection_id="unifranka",
            package_id="681496",
            episode_index=0,
            task_text="Clean the table with a broom.",
            task_index=None,
            family_map=mini_family_map,
        )


def test_resolve_task_family_unmapped_raises(mini_family_map: dict) -> None:
    with pytest.raises(TaskFamilyUnresolvedError, match="Unmapped"):
        resolve_task_family(
            collection_id="unifranka",
            package_id="681496",
            episode_index=0,
            task_text="Unknown task sentence.",
            task_index=None,
            family_map=mini_family_map,
        )


def test_is_single_family_package(mini_family_map: dict) -> None:
    assert is_single_family_package(mini_family_map, "681496") is True
    assert is_single_family_package(mini_family_map, "681460") is False


def test_resolve_schema_for_episode_with_active_job(mini_family_map: dict) -> None:
    job = AnnotationJob(
        job_id="unifranka-681460-wipe_clean-full",
        display_name="wipe sample",
        collection_id="unifranka",
        package_id="681460",
        task_family="wipe_clean",
        schema_id="wipe_clean_v1",
        scope="explicit",
        episode_indices=(562,),
    )
    result = resolve_schema_for_episode(
        collection_id="unifranka",
        package_id="681460",
        episode_index=562,
        task_text="Clean the table with a broom.",
        task_index=None,
        active_job=job,
        family_map=mini_family_map,
    )
    assert result.resolution_source == "active_job"
    assert result.schema_id == "wipe_clean_v1"
    assert result.task_family == "wipe_clean"
    assert result.l2_annotation_enabled is True
    assert any(lbl["id"] == "wipe" for lbl in result.schema["subtask_labels"])


def test_j01_job_family_mismatch_raises(mini_family_map: dict) -> None:
    job = AnnotationJob(
        job_id="unifranka-681460-pick_place-wrong",
        display_name="wrong",
        collection_id="unifranka",
        package_id="681460",
        task_family="pick_place",
        schema_id="pick_place_v1",
        scope="full",
    )
    with pytest.raises(JobFamilyMismatchError, match="J-01"):
        resolve_schema_for_episode(
            collection_id="unifranka",
            package_id="681460",
            episode_index=562,
            task_text="Clean the table with a broom.",
            task_index=None,
            active_job=job,
            family_map=mini_family_map,
        )


def test_j01_episode_outside_explicit_job_raises(mini_family_map: dict) -> None:
    job = AnnotationJob(
        job_id="unifranka-681460-wipe_clean-full",
        display_name="wipe sample",
        collection_id="unifranka",
        package_id="681460",
        task_family="wipe_clean",
        schema_id="wipe_clean_v1",
        scope="explicit",
        episode_indices=(562,),
    )
    with pytest.raises(JobFamilyMismatchError, match="outside job"):
        resolve_schema_for_episode(
            collection_id="unifranka",
            package_id="681460",
            episode_index=999,
            task_text="Clean the table with a broom.",
            task_index=None,
            active_job=job,
            family_map=mini_family_map,
        )


def test_resolve_schema_without_job_uses_family_map(mini_family_map: dict) -> None:
    result = resolve_schema_for_episode(
        collection_id="unifranka",
        package_id="681496",
        episode_index=0,
        task_text="Pick up an egg from the egg tray and place it on the plate.",
        task_index=0,
        family_map=mini_family_map,
    )
    assert result.resolution_source == "task_family_map"
    assert result.schema_id == "pick_place_v1"
    assert result.l2_annotation_enabled is True


def test_resolve_schema_unmapped_disables_l2(mini_family_map: dict) -> None:
    result = resolve_schema_for_episode(
        collection_id="unifranka",
        package_id="681460",
        episode_index=0,
        task_text="Totally unknown task.",
        task_index=None,
        family_map=mini_family_map,
    )
    assert result.l2_annotation_enabled is False
    assert result.unmapped_reason is not None


def test_legacy_package_schema_fallback(tmp_path: Path, mini_family_map: dict) -> None:
    pkg = tmp_path / "681496"
    (pkg / "meta").mkdir(parents=True)
    (pkg / "meta" / "info.json").write_text("{}", encoding="utf-8")
    (pkg / "annotation.schema.json").write_text(
        json.dumps(
            {
                "schema_id": "pick_place_v1",
                "schema_version": 1,
                "subtask_labels": [{"id": "reach", "order": 0, "color": "#000"}],
            }
        ),
        encoding="utf-8",
    )
    result = resolve_schema_for_episode(
        collection_id="unifranka",
        package_id="681496",
        episode_index=0,
        task_text=None,
        task_index=None,
        family_map=mini_family_map,
        package_root=pkg,
    )
    assert result.resolution_source == "legacy_package_schema"
    assert result.schema_id == "pick_place_v1"


@pytest.mark.skipif(not UNIFRANKA_MAP.is_file(), reason="UniFranka task-family-map not on disk")
def test_unifranka_map_covers_all_tasks() -> None:
    family_map = json.loads(UNIFRANKA_MAP.read_text(encoding="utf-8"))
    lookup = build_task_text_lookup(family_map)
    tasks = family_map.get("tasks") or []
    assert len(tasks) == family_map["summary"]["unique_tasks"]
    assert len(lookup) == len(tasks)
    for entry in tasks:
        text = entry["task_text"]
        family, ref = resolve_task_family(
            collection_id="unifranka",
            package_id=entry["packages"][0],
            episode_index=0,
            task_text=text,
            task_index=0,
            family_map=family_map,
        )
        assert family == entry["task_family"]
        assert ref == entry["schema_ref"]
        schema = load_schema_by_id(schema_id_from_ref(ref))
        assert schema["schema_id"] == schema_id_from_ref(entry["schema_ref"])


@pytest.mark.skipif(not UNIFRANKA_MAP.is_file(), reason="UniFranka task-family-map not on disk")
def test_load_task_family_map_from_collection_dir() -> None:
    loaded = load_task_family_map(UNIFRANKA_COLLECTION)
    assert loaded is not None
    assert loaded["collection_id"] == "unifranka"
    assert "pick_place" in loaded["schema_by_family"]
