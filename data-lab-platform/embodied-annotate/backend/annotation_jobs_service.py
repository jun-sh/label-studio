"""Annotation Job loading, episode filtering, and API helpers (D3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from annotation_job import AnnotationJob
from annotation_jobs_builder import ANNOTATION_JOBS_FILENAME
from annotation_schema import schema_ref
from episode_progress import episode_annotation_status
from task_family import (
    JobFamilyMismatchError,
    TaskFamilyUnresolvedError,
    load_schema_by_id as load_registry_schema,
    load_task_family_map,
    resolve_task_family,
)

MANIFEST_FILENAME = "collection.manifest.json"


def infer_collection_context(dataset_root: Path) -> tuple[str | None, str | None]:
    """Infer (collection_id, package_id) from a LeRobot package path."""
    root = dataset_root.resolve()
    package_id = root.name
    parent = root.parent
    manifest_path = parent / MANIFEST_FILENAME
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return parent.name, package_id
        collection_id = str(manifest.get("collection_id") or parent.name)
        return collection_id, package_id
    return None, package_id


def load_jobs_document(collection_dir: Path) -> dict[str, Any] | None:
    path = collection_dir / ANNOTATION_JOBS_FILENAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_collection_jobs(collection_dir: Path) -> list[AnnotationJob]:
    doc = load_jobs_document(collection_dir)
    if not doc:
        return []
    jobs: list[AnnotationJob] = []
    for raw in doc.get("jobs") or []:
        if isinstance(raw, dict) and raw.get("job_id"):
            jobs.append(AnnotationJob.from_dict(raw))
    return jobs


def package_requires_job(family_map: dict[str, Any] | None, package_id: str) -> bool:
    if not family_map:
        return False
    pkg = (family_map.get("packages") or {}).get(str(package_id))
    if not isinstance(pkg, dict):
        return False
    families = pkg.get("task_families") or {}
    active = sum(1 for count in families.values() if int(count or 0) > 0)
    return active > 1


def filter_jobs(
    jobs: list[AnnotationJob],
    *,
    package_id: str | None = None,
    task_family: str | None = None,
    status: str | None = None,
) -> list[AnnotationJob]:
    out = jobs
    if package_id is not None:
        out = [j for j in out if j.package_id == package_id]
    if task_family is not None:
        out = [j for j in out if j.task_family == task_family]
    if status is not None:
        out = [j for j in out if j.status == status]
    return sorted(out, key=lambda j: (j.priority, j.job_id))


def get_job_by_id(collection_dir: Path, job_id: str) -> AnnotationJob | None:
    for job in load_collection_jobs(collection_dir):
        if job.job_id == job_id:
            return job
    return None


def auto_select_open_job(collection_dir: Path, package_id: str) -> AnnotationJob | None:
    open_jobs = filter_jobs(
        load_collection_jobs(collection_dir),
        package_id=package_id,
        status="open",
    )
    if len(open_jobs) == 1:
        return open_jobs[0]
    return None


def compute_job_episode_indices(
    job: AnnotationJob,
    episodes_df: pd.DataFrame,
    *,
    family_map: dict[str, Any] | None,
    resolve_task_text: Callable[[pd.Series], tuple[str | None, int | None]],
) -> list[int]:
    if job.scope in {"sample", "explicit"}:
        allowed = {int(i) for i in job.episode_indices}
        return sorted(
            int(row["episode_index"])
            for _, row in episodes_df.iterrows()
            if int(row["episode_index"]) in allowed
        )

    indices: list[int] = []
    for _, row in episodes_df.iterrows():
        ep_idx = int(row["episode_index"])
        task_text, task_index = resolve_task_text(row)
        if family_map is None:
            indices.append(ep_idx)
            continue
        try:
            family, _ = resolve_task_family(
                collection_id=str(family_map.get("collection_id") or job.collection_id),
                package_id=job.package_id,
                episode_index=ep_idx,
                task_text=task_text,
                task_index=task_index,
                family_map=family_map,
            )
        except TaskFamilyUnresolvedError:
            continue
        if family == job.task_family:
            indices.append(ep_idx)
    return sorted(indices)


def job_schema_ref(job: AnnotationJob, *, package_root: Path | None = None) -> str:
    try:
        schema = load_registry_schema(job.schema_id, package_root=package_root)
        return schema_ref(schema)
    except FileNotFoundError:
        return f"{job.schema_id}@1"


def job_to_summary(
    job: AnnotationJob,
    *,
    episode_count: int,
    annotated_count: int,
) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "display_name": job.display_name,
        "collection_id": job.collection_id,
        "package_id": job.package_id,
        "task_family": job.task_family,
        "schema_id": job.schema_id,
        "schema_ref": job_schema_ref(job),
        "scope": job.scope,
        "status": job.status,
        "priority": job.priority,
        "episode_count": episode_count,
        "annotated_count": annotated_count,
    }


def count_annotated_episodes(
    episode_indices: list[int],
    annotations: dict[int, Any],
    schema: dict[str, Any],
) -> int:
    count = 0
    for ep_idx in episode_indices:
        ann = annotations.get(ep_idx)
        if ann is None:
            continue
        subtasks = ann.subtasks if hasattr(ann, "subtasks") else ann.get("subtasks")
        outcome = ann.outcome if hasattr(ann, "outcome") else ann.get("outcome")
        fields = ann.fields if hasattr(ann, "fields") else (ann.get("fields") or {})
        if episode_annotation_status(subtasks, outcome, fields, schema) == "complete":
            count += 1
    return count


def build_job_detail(
    job: AnnotationJob,
    *,
    collection_dir: Path,
    package_root: Path,
    episodes_df: pd.DataFrame,
    family_map: dict[str, Any] | None,
    annotations: dict[int, Any],
    resolve_task_text: Callable[[pd.Series], tuple[str | None, int | None]],
) -> dict[str, Any]:
    from task_family import resolve_schema_for_episode

    schema = load_registry_schema(job.schema_id, package_root=package_root)
    indices = compute_job_episode_indices(
        job,
        episodes_df,
        family_map=family_map,
        resolve_task_text=resolve_task_text,
    )
    fps = 5.0
    info_path = package_root / "meta" / "info.json"
    if info_path.is_file():
        fps = float(json.loads(info_path.read_text()).get("fps", 5))

    episodes_out: list[dict[str, Any]] = []
    for ep_idx in indices:
        row = episodes_df[episodes_df["episode_index"] == ep_idx]
        if row.empty:
            continue
        row = row.iloc[0]
        length = int(row.get("length", 0))
        task_text, task_index = resolve_task_text(row)
        resolution = resolve_schema_for_episode(
            collection_id=job.collection_id,
            package_id=job.package_id,
            episode_index=ep_idx,
            task_text=task_text,
            task_index=task_index,
            active_job=job,
            family_map=family_map,
            package_root=package_root,
        )
        ann = annotations.get(ep_idx)
        subtasks = ann.subtasks if ann and hasattr(ann, "subtasks") else None
        outcome = ann.outcome if ann and hasattr(ann, "outcome") else None
        fields = ann.fields if ann and hasattr(ann, "fields") else {}
        if ann and not hasattr(ann, "subtasks"):
            subtasks = ann.get("subtasks")
            outcome = ann.get("outcome")
            fields = ann.get("fields") or {}

        entry: dict[str, Any] = {
            "episode_index": ep_idx,
            "length": length,
            "duration": length / fps if fps else 0.0,
            "task_family": resolution.task_family,
            "schema_ref": resolution.schema_ref,
            "l2_annotation_enabled": resolution.l2_annotation_enabled,
            "annotation_status": (
                episode_annotation_status(subtasks, outcome, fields, resolution.schema)
                if ann is not None
                else "none"
            ),
        }
        if task_text:
            entry["task_text"] = task_text
        if task_index is not None:
            entry["task_index"] = task_index
        episodes_out.append(entry)

    annotated = count_annotated_episodes(indices, annotations, schema)
    summary = job_to_summary(job, episode_count=len(indices), annotated_count=annotated)
    return {
        **summary,
        "schema": schema,
        "episode_indices": indices,
        "episodes": episodes_out,
        "annotation_progress": {
            "complete": annotated,
            "partial": sum(
                1
                for ep in episodes_out
                if ep.get("annotation_status") == "partial"
            ),
            "total": len(indices),
            "complete_pct": round(100.0 * annotated / len(indices), 1) if indices else 0.0,
        },
    }


def resolve_job_for_load(
    *,
    collection_dir: Path,
    package_id: str,
    job_id: str | None,
    family_map: dict[str, Any] | None,
) -> AnnotationJob:
    jobs_exist = load_jobs_document(collection_dir) is not None
    if not jobs_exist:
        raise ValueError("no_jobs")

    if job_id:
        job = get_job_by_id(collection_dir, job_id)
        if job is None:
            raise KeyError(job_id)
        if job.package_id != package_id:
            raise JobFamilyMismatchError(
                f"Job {job_id} belongs to package {job.package_id}, not {package_id}"
            )
        if job.status != "open":
            raise PermissionError(f"Job {job_id} is {job.status}; only open jobs can be loaded")
        return job

    if package_requires_job(family_map, package_id):
        open_jobs = filter_jobs(
            load_collection_jobs(collection_dir),
            package_id=package_id,
            status="open",
        )
        if not job_id:
            all_pkg_jobs = filter_jobs(
                load_collection_jobs(collection_dir),
                package_id=package_id,
            )
            if not open_jobs:
                raise LookupError(
                    f"Package {package_id} requires job_id; no open jobs "
                    f"(available: {', '.join(j.job_id for j in all_pkg_jobs) or 'none'})"
                )
            if len(open_jobs) > 1:
                raise LookupError(
                    f"Package {package_id} requires job_id; open jobs: "
                    + ", ".join(j.job_id for j in open_jobs)
                )
            return open_jobs[0]

    auto = auto_select_open_job(collection_dir, package_id)
    if auto is not None:
        return auto

    open_jobs = filter_jobs(
        load_collection_jobs(collection_dir),
        package_id=package_id,
        status="open",
    )
    if len(open_jobs) > 1:
        raise LookupError(
            f"Multiple open jobs for package {package_id}; specify job_id"
        )
    if len(open_jobs) == 1:
        return open_jobs[0]
    raise ValueError("no_open_job")
