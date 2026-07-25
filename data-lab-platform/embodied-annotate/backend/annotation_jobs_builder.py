"""Build annotation.jobs.json from collection manifest + task-family-map (D2)."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

import pandas as pd

from task_family import (
    TASK_FAMILY_MAP_FILENAME,
    build_task_text_lookup,
    load_task_family_map,
)

ANNOTATION_JOBS_FILENAME = "annotation.jobs.json"
MANIFEST_FILENAME = "collection.manifest.json"
DEFAULT_SAMPLE_SEED = 20260720
DEFAULT_SAMPLE_COUNT = 30

PHASE_SPEC_BY_FAMILY: dict[str, str] = {
    "pick_place": "docs/unifranka-pick-place-phase-boundary-spec.md",
    "pour_transfer": "docs/schemas/pour_transfer_v1.annotation_schema.json",
    "tool_use": "docs/schemas/tool_use_v1.annotation_schema.json",
    "wipe_clean": "docs/schemas/wipe_clean_v1.annotation_schema.json",
    "articulate_drag": "docs/schemas/articulate_drag_v1.annotation_schema.json",
    "articulation": "docs/schemas/articulation_v1.annotation_schema.json",
    "box_transport": "docs/431132-phase-boundary-spec.md",
}

# P0 open; mixed-package jobs paused until P0 acceptance (annotation plan).
P0_PACKAGE_ORDER: dict[str, int] = {
    "681496": 10,
    "681458": 20,
    "681520": 30,
}

P1_SAMPLE_FAMILIES_681460 = frozenset({"pick_place", "wipe_clean"})

# Mixed packages: one open full-L2 job per task_family (all episodes in that family).
MIXED_FULL_PACKAGES: dict[str, int] = {
    "681460": 40,
    "681485": 50,
}


def extract_episode_task_text(row: pd.Series) -> str | None:
    """Mirror DataManager._resolve_episode_task_text (L0 from LeRobot meta)."""
    if "tasks" in row.index:
        tasks_val = row["tasks"]
        if tasks_val is not None and not (isinstance(tasks_val, float) and pd.isna(tasks_val)):
            if isinstance(tasks_val, str) and tasks_val.strip():
                return tasks_val.strip()
            if hasattr(tasks_val, "__len__") and not isinstance(tasks_val, str):
                try:
                    items = list(tasks_val)
                except TypeError:
                    items = []
                if items:
                    text = str(items[0]).strip()
                    if text:
                        return text
    return None


def load_package_episodes(package_root: Path) -> pd.DataFrame:
    ep_dir = package_root / "meta" / "episodes"
    files = sorted(ep_dir.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No episode parquet under {ep_dir}")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def index_episodes_by_family(
    episodes_df: pd.DataFrame,
    *,
    package_id: str,
    family_map: dict[str, Any],
) -> dict[str, dict[str, list[int]]]:
    """Return {task_family: {task_text: [episode_index, ...]}}."""
    lookup = build_task_text_lookup(family_map)
    by_family: dict[str, dict[str, list[int]]] = {}

    for _, row in episodes_df.iterrows():
        ep_idx = int(row["episode_index"])
        task_text = extract_episode_task_text(row)
        if not task_text:
            continue
        entry = lookup.get(task_text)
        if not entry:
            continue
        packages = [str(p) for p in (entry.get("packages") or [])]
        if packages and package_id not in packages:
            continue
        family = str(entry.get("task_family") or "")
        if not family:
            continue
        by_family.setdefault(family, {}).setdefault(task_text, []).append(ep_idx)

    for family in by_family:
        for text in by_family[family]:
            by_family[family][text] = sorted(by_family[family][text])
    return by_family


def stratified_sample_episodes(
    episodes_by_task_text: dict[str, list[int]],
    count: int,
    seed: int,
) -> list[int]:
    """Stratified sample by task_text with fixed seed (spec §3.5)."""
    rng = random.Random(seed)
    groups = {text: list(indices) for text, indices in sorted(episodes_by_task_text.items()) if indices}
    if not groups:
        return []

    total_available = sum(len(v) for v in groups.values())
    target = min(int(count), total_available)
    keys = sorted(groups)
    k = len(keys)
    base, rem = divmod(target, k)

    selected: list[int] = []
    for i, text in enumerate(keys):
        want = min(len(groups[text]), base + (1 if i < rem else 0))
        pool = list(groups[text])
        rng.shuffle(pool)
        selected.extend(pool[:want])

    if len(selected) < target:
        used = set(selected)
        remainder_pool: list[int] = []
        for text in keys:
            remainder_pool.extend(ep for ep in groups[text] if ep not in used)
        rng.shuffle(remainder_pool)
        selected.extend(remainder_pool[: target - len(selected)])

    if len(selected) > target:
        rng.shuffle(selected)
        selected = selected[:target]

    return sorted(selected)


def _schema_id_for_family(family_map: dict[str, Any], task_family: str) -> str:
    schema_by_family = family_map.get("schema_by_family") or {}
    schema_id = schema_by_family.get(task_family)
    if not schema_id:
        raise KeyError(f"No schema_by_family entry for {task_family!r}")
    return str(schema_id)


def _job_id(collection_id: str, package_id: str, task_family: str, scope: str, sample_count: int | None) -> str:
    if scope == "full":
        return f"{collection_id}-{package_id}-{task_family}-full"
    suffix = f"sample{sample_count or DEFAULT_SAMPLE_COUNT}"
    return f"{collection_id}-{package_id}-{task_family}-{suffix}"


def _display_name(package_id: str, task_family: str, scope: str, episode_count: int) -> str:
    if scope == "full":
        return f"{package_id} · {task_family} · 全量 L2 ({episode_count} ep)"
    return f"{package_id} · {task_family} · 抽样 {episode_count}"


def _default_status(package_id: str, task_family: str, scope: str) -> str:
    if package_id in P0_PACKAGE_ORDER and scope == "full":
        return "open"
    if package_id in MIXED_FULL_PACKAGES and scope == "full":
        return "open"
    if package_id == "681460" and task_family in P1_SAMPLE_FAMILIES_681460 and scope == "sample":
        return "paused"
    if package_id == "681485" and scope == "sample":
        return "paused"
    return "paused"


def _default_priority(package_id: str, task_family: str, scope: str, family_index: int) -> int:
    if package_id in P0_PACKAGE_ORDER and scope == "full":
        return P0_PACKAGE_ORDER[package_id]
    if package_id in MIXED_FULL_PACKAGES and scope == "full":
        return MIXED_FULL_PACKAGES[package_id] + family_index
    if package_id == "681460" and scope == "sample":
        base = 40
        order = {"pick_place": 0, "wipe_clean": 1, "pour_transfer": 2}
        return base + order.get(task_family, 10 + family_index)
    if package_id == "681485" and scope == "sample":
        return 50 + family_index
    return 100 + family_index


def build_job_record(
    *,
    collection_id: str,
    package_id: str,
    task_family: str,
    schema_id: str,
    scope: str,
    episode_indices: list[int],
    sample: dict[str, Any] | None = None,
    status: str | None = None,
    priority: int | None = None,
    family_index: int = 0,
    notes: str | None = None,
) -> dict[str, Any]:
    sample_count = len(episode_indices) if scope == "sample" else None
    job_id = _job_id(collection_id, package_id, task_family, scope, sample_count)
    record: dict[str, Any] = {
        "job_id": job_id,
        "display_name": _display_name(
            package_id,
            task_family,
            scope,
            len(episode_indices) if scope == "sample" else sum(1 for _ in episode_indices) or 0,
        ),
        "collection_id": collection_id,
        "package_id": package_id,
        "task_family": task_family,
        "schema_id": schema_id,
        "scope": scope,
        "status": status or _default_status(package_id, task_family, scope),
        "priority": priority if priority is not None else _default_priority(package_id, task_family, scope, family_index),
        "phase_spec_doc": PHASE_SPEC_BY_FAMILY.get(task_family),
    }
    if scope == "sample":
        record["sample"] = sample or {
            "method": "stratified",
            "count": DEFAULT_SAMPLE_COUNT,
            "seed": DEFAULT_SAMPLE_SEED,
            "stratify_by": "task_text",
        }
        record["episode_indices"] = episode_indices
        if notes:
            record["notes"] = notes
    elif notes:
        record["notes"] = notes
    return record


def _family_sample_seed(base_seed: int, task_family: str) -> int:
    digest = hashlib.md5(f"{base_seed}:{task_family}".encode()).hexdigest()
    return int(digest[:8], 16)


def build_jobs_for_package(
    *,
    collection_id: str,
    package_entry: dict[str, Any],
    family_map: dict[str, Any],
    package_root: Path,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    sample_seed: int = DEFAULT_SAMPLE_SEED,
) -> list[dict[str, Any]]:
    package_id = str(package_entry["id"])
    scope_policy = str(package_entry.get("annotation_scope") or "full_l2")
    per_family = index_episodes_by_family(
        load_package_episodes(package_root),
        package_id=package_id,
        family_map=family_map,
    )

    jobs: list[dict[str, Any]] = []
    families_sorted = sorted(per_family.keys())

    if scope_policy == "full_l2":
        dominant = package_entry.get("dominant_task_family")
        if dominant and dominant in per_family:
            families = [str(dominant)]
        elif len(families_sorted) == 1:
            families = families_sorted
        else:
            raise ValueError(f"Package {package_id} is full_l2 but has multiple families: {families_sorted}")

        for family in families:
            total_eps = sum(len(v) for v in per_family[family].values())
            schema_id = _schema_id_for_family(family_map, family)
            jobs.append(
                build_job_record(
                    collection_id=collection_id,
                    package_id=package_id,
                    task_family=family,
                    schema_id=schema_id,
                    scope="full",
                    episode_indices=[],
                    family_index=0,
                )
            )
            jobs[-1]["display_name"] = _display_name(package_id, family, "full", total_eps)
        return jobs

    if scope_policy == "full_by_family":
        for idx, family in enumerate(families_sorted):
            total_eps = sum(len(v) for v in per_family[family].values())
            if total_eps <= 0:
                continue
            schema_id = _schema_id_for_family(family_map, family)
            jobs.append(
                build_job_record(
                    collection_id=collection_id,
                    package_id=package_id,
                    task_family=family,
                    schema_id=schema_id,
                    scope="full",
                    episode_indices=[],
                    family_index=idx,
                )
            )
            jobs[-1]["display_name"] = _display_name(package_id, family, "full", total_eps)
        return jobs

    if scope_policy == "sample_by_family":
        recommended = package_entry.get("recommended_sample_per_family") or sample_count
        for idx, family in enumerate(families_sorted):
            groups = per_family[family]
            indices = stratified_sample_episodes(
                groups,
                int(recommended),
                _family_sample_seed(sample_seed, family),
            )
            if not indices:
                continue
            schema_id = _schema_id_for_family(family_map, family)
            notes = None
            status = _default_status(package_id, family, "sample")
            if package_id == "681460" and family == "pour_transfer":
                notes = "P1 未纳入；待 pick/wipe 抽样完成后开放"
                status = "paused"
            jobs.append(
                build_job_record(
                    collection_id=collection_id,
                    package_id=package_id,
                    task_family=family,
                    schema_id=schema_id,
                    scope="sample",
                    episode_indices=indices,
                    sample={
                        "method": "stratified",
                        "count": int(recommended),
                        "seed": sample_seed,
                        "stratify_by": "task_text",
                    },
                    status=status,
                    family_index=idx,
                    notes=notes,
                )
            )
        return jobs

    raise ValueError(f"Unsupported annotation_scope {scope_policy!r} for package {package_id}")


def build_annotation_jobs_document(
    collection_dir: Path,
    *,
    schema_registry_dir: str | None = None,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    sample_seed: int = DEFAULT_SAMPLE_SEED,
) -> dict[str, Any]:
    collection_dir = collection_dir.resolve()
    manifest_path = collection_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    collection_id = str(manifest.get("collection_id") or collection_dir.name)
    family_map = load_task_family_map(collection_dir)
    if family_map is None:
        raise FileNotFoundError(collection_dir / TASK_FAMILY_MAP_FILENAME)

    jobs: list[dict[str, Any]] = []
    for raw_pkg in manifest.get("packages") or []:
        if not isinstance(raw_pkg, dict):
            continue
        pkg_id = str(raw_pkg.get("id") or "")
        if not pkg_id:
            continue
        package_root = collection_dir / pkg_id
        if not package_root.is_dir():
            continue
        jobs.extend(
            build_jobs_for_package(
                collection_id=collection_id,
                package_entry={**raw_pkg, "id": pkg_id},
                family_map=family_map,
                package_root=package_root,
                sample_count=sample_count,
                sample_seed=sample_seed,
            )
        )

    jobs.sort(key=lambda j: (int(j.get("priority") or 999), j.get("job_id") or ""))
    registry = schema_registry_dir or "data-lab-platform/embodied-annotate/docs/schemas"
    return {
        "jobs_version": "1.0",
        "collection_id": collection_id,
        "schema_registry_dir": registry,
        "task_family_map": TASK_FAMILY_MAP_FILENAME,
        "generated_by": "backend/tools/generate_annotation_jobs.py",
        "sample_seed": sample_seed,
        "jobs": jobs,
    }


def write_annotation_jobs(
    collection_dir: Path,
    output_path: Path | None = None,
    **kwargs: Any,
) -> Path:
    doc = build_annotation_jobs_document(collection_dir, **kwargs)
    out = output_path or (collection_dir.resolve() / ANNOTATION_JOBS_FILENAME)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out
