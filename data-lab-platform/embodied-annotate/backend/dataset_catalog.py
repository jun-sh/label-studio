"""Discover LeRobot dataset collections under the embodied-annotate datasets root."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from annotation_schema import resolve_annotation_schema, schema_ref

MANIFEST_FILENAME = "collection.manifest.json"
INFO_FILENAME = "meta/info.json"


def datasets_root() -> Path:
    return Path(os.environ.get("LEROBOT_ANNOTATE_DATASETS", "/data/datasets")).expanduser().resolve()


def _is_lerobot_root(path: Path) -> bool:
    return (path / INFO_FILENAME).is_file()


def _video_keys_from_info(info: dict[str, Any]) -> list[str]:
    features = info.get("features") or {}
    return sorted(k for k, meta in features.items() if isinstance(meta, dict) and meta.get("dtype") == "video")


def _task_preview(info: dict[str, Any], package_root: Path) -> str | None:
    tasks_path = package_root / "meta" / "tasks.parquet"
    if not tasks_path.is_file():
        return None
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(tasks_path, columns=["task"])
        if table.num_rows == 0:
            return None
        value = table.column("task")[0].as_py()
        return str(value) if value is not None else None
    except Exception:
        return None


def _enrich_package(package_root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    info_path = package_root / INFO_FILENAME
    info: dict[str, Any] = {}
    if info_path.is_file():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            info = {}

    video_keys = entry.get("video_keys")
    if not video_keys and info:
        video_keys = _video_keys_from_info(info)

    local_path = entry.get("local_path")
    if local_path:
        resolved = Path(str(local_path)).expanduser()
        if not resolved.is_absolute():
            base = entry.get("_collection_dir") or package_root.parent
            resolved = base / resolved
        local_path = str(resolved.resolve())
    else:
        local_path = str(package_root.resolve())

    task_preview = entry.get("task_preview") or _task_preview(info, package_root)

    return {
        "id": entry.get("id") or package_root.name,
        "display_name": entry.get("display_name") or package_root.name,
        "local_path": local_path,
        "structure_type": entry.get("structure_type"),
        "resolution": entry.get("resolution"),
        "episodes": entry.get("episodes", info.get("total_episodes")),
        "frames": entry.get("frames", info.get("total_frames")),
        "fps": entry.get("fps", info.get("fps")),
        "robot_type": entry.get("robot_type", info.get("robot_type")),
        "video_keys": video_keys or [],
        "state_semantic": entry.get("state_semantic"),
        "annotation_status": entry.get("annotation_status"),
        "task_preview": task_preview,
        "codebase_version": info.get("codebase_version"),
        "loadable": _is_lerobot_root(package_root),
    }


def _load_manifest_collection(collection_dir: Path) -> dict[str, Any] | None:
    manifest_path = collection_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "id": collection_dir.name,
            "title": collection_dir.name,
            "description": f"Invalid manifest: {exc}",
            "packages": [],
            "error": "invalid_manifest",
        }

    collection_id = manifest.get("collection_id") or collection_dir.name
    packages_out: list[dict[str, Any]] = []
    for raw_pkg in manifest.get("packages") or []:
        if not isinstance(raw_pkg, dict):
            continue
        pkg_id = str(raw_pkg.get("id") or "").strip()
        if not pkg_id:
            continue
        pkg_root = collection_dir / pkg_id
        if not pkg_root.is_dir():
            continue
        pkg_entry = dict(raw_pkg)
        pkg_entry["_collection_dir"] = collection_dir
        enriched = _enrich_package(pkg_root, pkg_entry)
        enriched["annotation_schema"] = resolve_annotation_schema(pkg_root)
        enriched["schema_ref"] = schema_ref(enriched["annotation_schema"])
        packages_out.append(enriched)

    collection_schema = manifest.get("annotation_schema")
    return {
        "id": collection_id,
        "title": manifest.get("title") or collection_id,
        "description": manifest.get("description"),
        "robot_type": manifest.get("robot_type"),
        "task_family": manifest.get("task_family"),
        "package_count": len(packages_out),
        "annotation_schema": collection_schema,
        "schema_ref": schema_ref(collection_schema) if collection_schema else None,
        "packages": packages_out,
    }


def _leaf_collection(collection_dir: Path) -> dict[str, Any] | None:
    if not _is_lerobot_root(collection_dir):
        return None
    pkg = _enrich_package(collection_dir, {"id": collection_dir.name, "display_name": collection_dir.name})
    ann_schema = resolve_annotation_schema(collection_dir)
    title = collection_dir.name
    pkg["annotation_schema"] = ann_schema
    pkg["schema_ref"] = schema_ref(ann_schema)
    return {
        "id": collection_dir.name,
        "title": title,
        "description": "LeRobot v3 leaf dataset",
        "package_count": 1,
        "annotation_schema": ann_schema,
        "schema_ref": schema_ref(ann_schema),
        "packages": [pkg],
        "kind": "leaf",
    }


def list_collections(*, refresh: bool = False) -> list[dict[str, Any]]:
    del refresh  # reserved for future caching
    root = datasets_root()
    if not root.is_dir():
        return []

    collections: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        manifest_collection = _load_manifest_collection(entry)
        if manifest_collection is not None:
            collections.append(manifest_collection)
            continue
        leaf = _leaf_collection(entry)
        if leaf is not None:
            collections.append(leaf)

  # Primary corpora first, then alphabetical
    collections.sort(key=lambda c: (c.get("id") != "limx_box_transport", c.get("title") or c.get("id") or ""))
    return collections


def get_collection(collection_id: str) -> dict[str, Any]:
    for collection in list_collections():
        if collection.get("id") == collection_id:
            return collection
    raise KeyError(collection_id)


def resolve_package_path(collection_id: str, package_id: str) -> str:
    collection = get_collection(collection_id)
    for pkg in collection.get("packages") or []:
        if pkg.get("id") == package_id:
            path = pkg.get("local_path")
            if path:
                return str(path)
    raise KeyError(f"{collection_id}/{package_id}")
