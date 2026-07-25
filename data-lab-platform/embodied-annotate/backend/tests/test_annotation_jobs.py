"""Tests for annotation.jobs.json generation (D2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from annotation_job import AnnotationJob
from annotation_jobs_builder import (
    build_annotation_jobs_document,
    stratified_sample_episodes,
    write_annotation_jobs,
)
from task_family import load_jobs_file

WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
UNIFRANKA_COLLECTION = WORKSPACE_ROOT / "data-storage/embodied-annotate/datasets/unifranka"


def test_stratified_sample_reproducible() -> None:
    groups = {
        "task_a": list(range(0, 50)),
        "task_b": list(range(50, 100)),
    }
    a = stratified_sample_episodes(groups, 30, 20260720)
    b = stratified_sample_episodes(groups, 30, 20260720)
    assert a == b
    assert len(a) == 30


def test_stratified_sample_caps_when_pool_smaller() -> None:
    groups = {"only": [1, 2, 3]}
    picked = stratified_sample_episodes(groups, 30, 99)
    assert picked == [1, 2, 3]


@pytest.mark.skipif(not UNIFRANKA_COLLECTION.is_dir(), reason="UniFranka collection not on disk")
def test_build_unifranka_jobs_document() -> None:
    doc = build_annotation_jobs_document(UNIFRANKA_COLLECTION)
    assert doc["collection_id"] == "unifranka"
    assert doc["jobs_version"] == "1.0"
    jobs = doc["jobs"]
    assert len(jobs) == 12

    by_id = {j["job_id"]: j for j in jobs}
    assert "unifranka-681496-pick_place-full" in by_id
    assert "unifranka-681458-pick_place-full" in by_id
    assert "unifranka-681520-pick_place-full" in by_id
    assert by_id["unifranka-681496-pick_place-full"]["status"] == "open"
    assert by_id["unifranka-681496-pick_place-full"]["priority"] == 10

    wipe = by_id["unifranka-681460-wipe_clean-full"]
    assert wipe["scope"] == "full"
    assert wipe["schema_id"] == "wipe_clean_v1"
    assert wipe["status"] == "open"
    assert "抽样" not in wipe["display_name"]

    pour = by_id["unifranka-681460-pour_transfer-full"]
    assert pour["scope"] == "full"
    assert pour["status"] == "open"

    p2_jobs = [j for j in jobs if j["package_id"] == "681485"]
    assert len(p2_jobs) == 6
    assert all(j["status"] == "open" for j in p2_jobs)
    assert all(j["scope"] == "full" for j in p2_jobs)


@pytest.mark.skipif(not UNIFRANKA_COLLECTION.is_dir(), reason="UniFranka collection not on disk")
def test_generated_jobs_loadable(tmp_path: Path) -> None:
    out = write_annotation_jobs(UNIFRANKA_COLLECTION, output_path=tmp_path / "annotation.jobs.json")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["jobs"]) == 12
    loaded = load_jobs_file(tmp_path)
    assert len(loaded) == 12
    job = next(j for j in loaded if j.job_id.endswith("681496-pick_place-full"))
    assert job.schema_id == "pick_place_v1"
    assert job.scope == "full"
    assert job.episode_allowed(999) is True

    mixed = next(j for j in loaded if j.job_id == "unifranka-681460-wipe_clean-full")
    assert mixed.scope == "full"
    assert mixed.episode_indices == ()
