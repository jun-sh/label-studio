"""Task family resolution and per-episode schema routing (annotation_job_spec@1.0 · D1)."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from annotation_job import AnnotationJob
from annotation_schema import (
    DEFAULT_MANIPULATION_SCHEMA,
    _load_json_file,
    resolve_annotation_schema,
    schema_ref,
)

TASK_FAMILY_MAP_FILENAME = "task-family-map.json"
SCHEMA_FILE_SUFFIX = ".annotation_schema.json"
ANNOTATION_JOBS_FILENAME = "annotation.jobs.json"

APP_ROOT = Path(__file__).resolve().parent
DEFAULT_SCHEMA_REGISTRY_DIR = APP_ROOT.parent / "docs" / "schemas"


class TaskFamilyError(Exception):
    """Base error for task-family / job resolution."""


class TaskFamilyUnresolvedError(TaskFamilyError):
    """task_text is not present in the collection task-family map (J-02)."""


class JobFamilyMismatchError(TaskFamilyError):
    """Episode task_family does not match the active annotation job (J-01)."""


@dataclass(frozen=True)
class EpisodeSchemaResolution:
    """Result of resolve_schema_for_episode."""

    schema: dict[str, Any]
    schema_ref: str
    schema_id: str
    task_family: str | None
    l2_annotation_enabled: bool
    resolution_source: str
    unmapped_reason: str | None = None
    job_mismatch: bool = False


def schema_registry_candidates() -> list[Path]:
    """Ordered search paths for ``{schema_id}.annotation_schema.json``."""
    candidates: list[Path] = []
    env = os.environ.get("LEROBOT_ANNOTATE_SCHEMA_REGISTRY")
    if env:
        candidates.append(Path(env).expanduser().resolve())
    candidates.extend(
        [
            APP_ROOT.parent / "docs" / "schemas",
            APP_ROOT / "schemas",
            Path("/app/docs/schemas"),
        ]
    )
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def default_schema_registry_dir() -> Path:
    for path in schema_registry_candidates():
        if path.is_dir():
            return path
    return schema_registry_candidates()[0]


def load_task_family_map(collection_dir: Path) -> dict[str, Any] | None:
    path = collection_dir.resolve() / TASK_FAMILY_MAP_FILENAME
    return _load_json_file(path)


def build_task_text_lookup(family_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for entry in family_map.get("tasks") or []:
        if not isinstance(entry, dict):
            continue
        text = entry.get("task_text")
        if text is None:
            continue
        lookup[str(text)] = entry
    return lookup


def schema_id_from_ref(schema_ref_value: str) -> str:
    return str(schema_ref_value).split("@", 1)[0]


def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(schema)
    out.setdefault("validation", {})
    out["validation"].setdefault("gap_warn_frames", 10)
    out["validation"].setdefault("segments_must_not_overlap", True)
    out.setdefault("episode_fields", [])
    out.setdefault("cycle_fields", [])
    return out


def load_schema_by_id(
    schema_id: str,
    registry_dir: Path | None = None,
    *,
    package_root: Path | None = None,
) -> dict[str, Any]:
    """Load ``docs/schemas/{schema_id}.annotation_schema.json``."""
    search_dirs = [registry_dir.resolve()] if registry_dir else schema_registry_candidates()
    tried: list[Path] = []
    for root in search_dirs:
        path = root / f"{schema_id}{SCHEMA_FILE_SUFFIX}"
        tried.append(path)
        data = _load_json_file(path)
        if data and data.get("subtask_labels"):
            return _normalize_schema(data)

    if package_root is not None:
        legacy = resolve_annotation_schema(package_root)
        if legacy.get("schema_id") == schema_id and legacy.get("subtask_labels"):
            return legacy

    raise FileNotFoundError(
        f"Schema registry entry not found for {schema_id!r}; tried: {', '.join(str(p) for p in tried)}"
    )


def is_single_family_package(family_map: dict[str, Any] | None, package_id: str) -> bool:
    if not family_map:
        return False
    pkg = (family_map.get("packages") or {}).get(str(package_id))
    if not isinstance(pkg, dict):
        return False
    families = pkg.get("task_families") or {}
    active = [name for name, count in families.items() if int(count or 0) > 0]
    return len(active) == 1


def resolve_task_family(
    *,
    collection_id: str,
    package_id: str,
    episode_index: int,
    task_text: str | None,
    task_index: int | None,
    family_map: dict[str, Any],
    package_root: Path | None = None,
) -> tuple[str, str]:
    """Resolve (task_family, schema_ref) for an episode via task-family-map."""
    del collection_id, episode_index, task_index  # reserved for future stratified rules

    if not task_text or not str(task_text).strip():
        raise TaskFamilyUnresolvedError("Episode has no task_text")

    lookup = build_task_text_lookup(family_map)
    entry = lookup.get(str(task_text).strip())
    if entry is None:
        raise TaskFamilyUnresolvedError(f"Unmapped task_text: {task_text!r}")

    packages = [str(p) for p in (entry.get("packages") or [])]
    if packages and str(package_id) not in packages:
        raise TaskFamilyUnresolvedError(
            f"task_text {task_text!r} is not registered for package {package_id}"
        )

    task_family = str(entry.get("task_family") or "")
    if not task_family:
        schema_by_family = family_map.get("schema_by_family") or {}
        raise TaskFamilyUnresolvedError(
            f"task_text {task_text!r} has no task_family in map"
        )

    ref = entry.get("schema_ref")
    if ref:
        return task_family, str(ref)

    schema_id = (family_map.get("schema_by_family") or {}).get(task_family)
    if not schema_id:
        raise TaskFamilyUnresolvedError(f"No schema mapping for task_family {task_family!r}")

    schema = load_schema_by_id(str(schema_id), package_root=package_root)
    return task_family, schema_ref(schema)


def _resolve_from_family_map(
    *,
    package_id: str,
    task_text: str | None,
    task_index: int | None,
    family_map: dict[str, Any],
    registry_dir: Path | None,
    package_root: Path | None = None,
) -> tuple[dict[str, Any], str, str, str]:
    task_family, ref = resolve_task_family(
        collection_id=str(family_map.get("collection_id") or ""),
        package_id=package_id,
        episode_index=-1,
        task_text=task_text,
        task_index=task_index,
        family_map=family_map,
        package_root=package_root,
    )
    schema_id = schema_id_from_ref(ref)
    schema = load_schema_by_id(schema_id, registry_dir, package_root=package_root)
    return schema, ref, schema_id, task_family


def resolve_schema_for_episode(
    *,
    collection_id: str,
    package_id: str,
    episode_index: int,
    task_text: str | None,
    task_index: int | None,
    active_job: AnnotationJob | None = None,
    family_map: dict[str, Any] | None = None,
    package_root: Path | None = None,
    registry_dir: Path | None = None,
    allow_default_fallback: bool = False,
) -> EpisodeSchemaResolution:
    """
    Resolve the annotation schema for one episode.

    Resolution order (frozen in annotation-job-spec §4.2):
      1. active_job.schema_id (with J-01 family check)
      2. task_text → task-family-map → schema registry
      3. single-family legacy package annotation.schema.json
      4. default_manipulation_v1 (annotation disabled unless allow_default_fallback)
    """
    del collection_id

    if active_job is not None:
        if str(active_job.package_id) != str(package_id):
            raise JobFamilyMismatchError(
                f"Active job package {active_job.package_id!r} != loaded package {package_id!r}"
            )
        if not active_job.episode_allowed(episode_index):
            raise JobFamilyMismatchError(
                f"Episode {episode_index} is outside job {active_job.job_id!r} queue"
            )

        episode_family: str | None = None
        family_mismatch = False
        unmapped_reason: str | None = None

        if family_map is not None:
            try:
                episode_family, _ = resolve_task_family(
                    collection_id=str(family_map.get("collection_id") or ""),
                    package_id=package_id,
                    episode_index=episode_index,
                    task_text=task_text,
                    task_index=task_index,
                    family_map=family_map,
                )
                if episode_family != active_job.task_family:
                    family_mismatch = True
            except TaskFamilyUnresolvedError as exc:
                unmapped_reason = str(exc)

        if family_mismatch:
            raise JobFamilyMismatchError(
                f"Episode {episode_index} task_family {episode_family!r} "
                f"does not match job {active_job.task_family!r} (J-01)"
            )

        schema = load_schema_by_id(
            active_job.schema_id,
            registry_dir,
            package_root=package_root,
        )
        ref = schema_ref(schema)
        return EpisodeSchemaResolution(
            schema=schema,
            schema_ref=ref,
            schema_id=active_job.schema_id,
            task_family=active_job.task_family,
            l2_annotation_enabled=unmapped_reason is None,
            resolution_source="active_job",
            unmapped_reason=unmapped_reason,
            job_mismatch=False,
        )

    if family_map is not None:
        try:
            schema, ref, schema_id, task_family = _resolve_from_family_map(
                package_id=package_id,
                task_text=task_text,
                task_index=task_index,
                family_map=family_map,
                registry_dir=registry_dir,
                package_root=package_root,
            )
            return EpisodeSchemaResolution(
                schema=schema,
                schema_ref=ref,
                schema_id=schema_id,
                task_family=task_family,
                l2_annotation_enabled=True,
                resolution_source="task_family_map",
            )
        except TaskFamilyUnresolvedError as exc:
            if package_root is not None and is_single_family_package(family_map, package_id):
                legacy = resolve_annotation_schema(package_root)
                if legacy.get("schema_id") != DEFAULT_MANIPULATION_SCHEMA["schema_id"]:
                    return EpisodeSchemaResolution(
                        schema=legacy,
                        schema_ref=schema_ref(legacy),
                        schema_id=str(legacy.get("schema_id") or "unknown"),
                        task_family=(family_map.get("packages") or {})
                        .get(str(package_id), {})
                        .get("dominant_family"),
                        l2_annotation_enabled=False,
                        resolution_source="legacy_package_schema",
                        unmapped_reason=str(exc),
                    )
            return EpisodeSchemaResolution(
                schema=_normalize_schema(deepcopy(DEFAULT_MANIPULATION_SCHEMA)),
                schema_ref=schema_ref(DEFAULT_MANIPULATION_SCHEMA),
                schema_id=DEFAULT_MANIPULATION_SCHEMA["schema_id"],
                task_family=None,
                l2_annotation_enabled=False,
                resolution_source="unmapped",
                unmapped_reason=str(exc),
            )

    if package_root is not None:
        legacy = resolve_annotation_schema(package_root)
        if legacy.get("schema_id") != DEFAULT_MANIPULATION_SCHEMA["schema_id"]:
            return EpisodeSchemaResolution(
                schema=legacy,
                schema_ref=schema_ref(legacy),
                schema_id=str(legacy.get("schema_id") or "unknown"),
                task_family=None,
                l2_annotation_enabled=True,
                resolution_source="legacy_package_schema",
            )

    default_schema = _normalize_schema(deepcopy(DEFAULT_MANIPULATION_SCHEMA))
    return EpisodeSchemaResolution(
        schema=default_schema,
        schema_ref=schema_ref(default_schema),
        schema_id=default_schema["schema_id"],
        task_family=None,
        l2_annotation_enabled=allow_default_fallback,
        resolution_source="default_fallback",
        unmapped_reason=None if allow_default_fallback else "No task-family map or legacy schema",
    )


def load_jobs_file(collection_dir: Path) -> list[AnnotationJob]:
    """Load ``annotation.jobs.json`` when present (D2)."""
    path = collection_dir.resolve() / ANNOTATION_JOBS_FILENAME
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    jobs: list[AnnotationJob] = []
    for raw in payload.get("jobs") or []:
        if isinstance(raw, dict) and raw.get("job_id"):
            jobs.append(AnnotationJob.from_dict(raw))
    return jobs
