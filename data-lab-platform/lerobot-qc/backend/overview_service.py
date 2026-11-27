"""Read-only QC overview across all packages of a collection.

Reads sidecar manifests directly: opening a QcStore per package would create a sidecar
for every package that was never reviewed.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from dataset_catalog import get_collection
from qc_store import (
    canonical_dataset_key,
    manifest_active_rebuild_job,
    manifest_delivery_status,
    manifest_last_qc_change_at,
    manifest_review_counts,
)

logger = logging.getLogger(__name__)

# Ordered by "what to do next"; the UI filters and sorts on these.
STAGES = ("rebuilding", "suspicious", "needs_rebuild", "in_review", "not_started", "delivered", "all_removed")


_manifest_cache: dict[Path, tuple[int, int, dict[str, Any]]] = {}
_manifest_cache_lock = threading.Lock()


def _read_manifest(path: Path) -> dict[str, Any] | None:
    """Parse a sidecar manifest, reusing the previous parse while (mtime, size) is unchanged."""
    try:
        stat = path.stat()
    except OSError:
        return None
    signature = (stat.st_mtime_ns, stat.st_size)
    with _manifest_cache_lock:
        cached = _manifest_cache.get(path)
    if cached and cached[:2] == signature:
        return cached[2]
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Unreadable sidecar manifest: %s", path)
        return None
    with _manifest_cache_lock:
        _manifest_cache[path] = (*signature, manifest)
    return manifest


def index_sidecars(sidecar_base: Path) -> dict[str, dict[str, Any]]:
    """canonical dataset key -> manifest; on duplicates keep the one with most reviews
    (same tie-break as qc_store.resolve_sidecar_dir)."""
    index: dict[str, tuple[int, str, dict[str, Any]]] = {}
    if not sidecar_base.is_dir():
        return {}
    for entry in sidecar_base.iterdir():
        manifest = _read_manifest(entry / "qc_manifest.json")
        if manifest is None:
            continue
        stored_root = manifest.get("dataset_root")
        if not stored_root:
            continue
        key = canonical_dataset_key(Path(str(stored_root)), resolve=False)
        rank = (len(manifest.get("reviews") or {}), str(entry))
        if key not in index or rank > index[key][:2]:
            index[key] = (*rank, manifest)
    return {key: value[2] for key, value in index.items()}


def _stage(counts: dict[str, int], delivery: dict[str, Any], rebuilding: bool, reviewed: int) -> str:
    if rebuilding:
        return "rebuilding"
    if counts["total"] > 0 and counts["rejected"] >= counts["total"]:
        return "all_removed"
    if counts["suspicious"] > 0:
        return "suspicious"
    if reviewed == 0:
        return "not_started"
    if counts["pending"] > 0:
        return "in_review"
    if delivery["state"] == "current":
        return "delivered"
    return "needs_rebuild"


def package_overview(pkg: dict[str, Any], manifest: dict[str, Any] | None, running_jobs: set[str]) -> dict[str, Any]:
    manifest = manifest or {}
    total = int(pkg.get("episodes") or 0)
    counts = manifest_review_counts(manifest, total)
    delivery = manifest_delivery_status(manifest)
    active = manifest_active_rebuild_job(manifest)
    rebuilding = bool(active and active.get("job_id") in running_jobs)
    # An episode explicitly reset to "pending" counts as not reviewed.
    reviewed = max(0, min(total, total - counts["pending"]))
    return {
        "id": pkg.get("id"),
        "local_path": pkg.get("local_path"),
        "loadable": bool(pkg.get("loadable", True)),
        "episodes": total,
        "reviewed": reviewed,
        **{k: counts[k] for k in ("approved", "rejected", "suspicious", "pending")},
        "instruction_overrides": len(manifest.get("instruction_overrides") or {}),
        "last_qc_change_at": manifest_last_qc_change_at(manifest),
        "delivery": delivery,
        "rebuild_job": active if rebuilding else None,
        "stage": _stage(counts, delivery, rebuilding, reviewed),
    }


def build_overview(
    collection_id: str,
    sidecar_base: Path,
    running_jobs: set[str],
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    collection = get_collection(collection_id, force_refresh=refresh)
    sidecars = index_sidecars(sidecar_base)
    packages = []
    for pkg in collection.get("packages") or []:
        local_path = pkg.get("local_path")
        manifest = sidecars.get(canonical_dataset_key(Path(str(local_path)), resolve=False)) if local_path else None
        packages.append(package_overview(pkg, manifest, running_jobs))
    stage_counts = {stage: 0 for stage in STAGES}
    for row in packages:
        stage_counts[row["stage"]] += 1
    totals = {
        key: sum(row[key] for row in packages)
        for key in ("episodes", "reviewed", "approved", "rejected", "suspicious", "pending", "instruction_overrides")
    }
    return {
        "collection": {k: collection.get(k) for k in ("id", "title", "description", "robot_type")},
        "packages": packages,
        "stage_counts": stage_counts,
        "totals": totals,
    }
