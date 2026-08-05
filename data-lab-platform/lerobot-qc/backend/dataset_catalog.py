"""Discover LeRobot v3.0 datasets under the QC datasets root (no annotation logic)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from storage_manager import get_active_storage_root

MANIFEST_FILENAME = "collection.manifest.json"
INFO_FILENAME = "meta/info.json"
V3_CODEBASE = "v3.0"


def datasets_root() -> Path:
    return Path(os.environ.get("LEROBOT_QC_DATASETS", os.environ.get("LEROBOT_ANNOTATE_DATASETS", "/data/datasets"))).expanduser().resolve()


def collections_registry_path() -> Path | None:
    raw = os.environ.get("LEROBOT_QC_COLLECTIONS_REGISTRY", "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _collection_allowlist() -> set[str] | None:
    raw = os.environ.get("LEROBOT_QC_COLLECTION_ALLOWLIST", "").strip()
    if not raw:
        return None
    ids = {item.strip() for item in raw.split(",") if item.strip()}
    return ids or None


def _is_lerobot_root(path: Path) -> bool:
    return (path / INFO_FILENAME).is_file()


def _read_info(package_root: Path) -> dict[str, Any]:
    info_path = package_root / INFO_FILENAME
    if not info_path.is_file():
        return {}
    try:
        return json.loads(info_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _video_keys_from_info(info: dict[str, Any]) -> list[str]:
    features = info.get("features") or {}
    return sorted(k for k, meta in features.items() if isinstance(meta, dict) and meta.get("dtype") == "video")


def _task_preview(package_root: Path) -> str | None:
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
    info = _read_info(package_root)
    video_keys = entry.get("video_keys") or _video_keys_from_info(info)

    local_path = entry.get("local_path")
    if local_path:
        resolved = Path(str(local_path)).expanduser()
        if not resolved.is_absolute():
            base = entry.get("_collection_dir") or package_root.parent
            resolved = base / resolved
        local_path = str(resolved.resolve())
    else:
        local_path = str(package_root.resolve())

    codebase_version = info.get("codebase_version")
    return {
        "id": entry.get("id") or package_root.name,
        "display_name": entry.get("display_name") or package_root.name,
        "local_path": local_path,
        "episodes": entry.get("episodes", info.get("total_episodes")),
        "frames": entry.get("frames", info.get("total_frames")),
        "fps": entry.get("fps", info.get("fps")),
        "robot_type": entry.get("robot_type", info.get("robot_type")),
        "video_keys": video_keys or [],
        "task_preview": entry.get("task_preview") or _task_preview(package_root),
        "codebase_version": codebase_version,
        "loadable": _is_lerobot_root(package_root) and str(codebase_version).strip() == V3_CODEBASE,
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
        packages_out.append(_enrich_package(pkg_root, pkg_entry))

    return {
        "id": collection_id,
        "title": manifest.get("title") or collection_id,
        "description": manifest.get("description"),
        "robot_type": manifest.get("robot_type"),
        "package_count": len(packages_out),
        "packages": packages_out,
        "kind": "collection",
    }


def _scan_package_collection(
    collection_dir: Path,
    *,
    collection_id: str,
    title: str | None = None,
    description: str | None = None,
    robot_type: str | None = None,
) -> dict[str, Any] | None:
    packages_out: list[dict[str, Any]] = []
    for entry in sorted(collection_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if not _is_lerobot_root(entry):
            continue
        pkg_entry = {"id": entry.name, "_collection_dir": collection_dir}
        packages_out.append(_enrich_package(entry, pkg_entry))

    if not packages_out:
        return None

    resolved_robot_type = robot_type
    if not resolved_robot_type:
        for pkg in packages_out:
            if pkg.get("robot_type"):
                resolved_robot_type = pkg["robot_type"]
                break

    return {
        "id": collection_id,
        "title": title or collection_id,
        "description": description or "LeRobot v3 multi-package collection",
        "robot_type": resolved_robot_type,
        "package_count": len(packages_out),
        "packages": packages_out,
        "kind": "collection",
    }


def _load_collections_registry() -> dict[str, Any] | None:
    registry_path = collections_registry_path()
    if registry_path is None or not registry_path.is_file():
        return None
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return registry if isinstance(registry, dict) else None


def _registry_storage_root(registry: dict[str, Any]) -> Path:
    active = get_active_storage_root()
    if active is not None:
        return active
    return Path(str(registry.get("storage_root") or "/data/bookduo")).expanduser()


def _resolve_registry_collection_dir(registry: dict[str, Any], entry: dict[str, Any]) -> Path | None:
    raw_path = str(entry.get("path") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path.resolve() if path.is_dir() else None
    storage_root = _registry_storage_root(registry)
    collection_dir = (storage_root / raw_path).resolve()
    return collection_dir if collection_dir.is_dir() else None


def _registry_collection_dir(registry: dict[str, Any], entry: dict[str, Any]) -> Path | None:
    raw_path = str(entry.get("path") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (_registry_storage_root(registry) / raw_path).resolve()


def _load_registry_collection(registry: dict[str, Any], entry: dict[str, Any], *, include_unavailable: bool = False) -> dict[str, Any] | None:
    collection_id = str(entry.get("id") or "").strip()
    if not collection_id:
        return None

    collection_dir = _resolve_registry_collection_dir(registry, entry)
    expected_dir = _registry_collection_dir(registry, entry)
    if collection_dir is None:
        if not include_unavailable:
            return None
        return {
            "id": collection_id,
            "title": entry.get("title") or collection_id,
            "description": entry.get("description"),
            "robot_type": entry.get("robot_type"),
            "package_count": 0,
            "packages": [],
            "kind": "collection",
            "status": "unavailable",
            "expected_path": str(expected_dir) if expected_dir else None,
        }

    if (collection_dir / MANIFEST_FILENAME).is_file():
        collection = _load_manifest_collection(collection_dir)
    else:
        collection = _scan_package_collection(
            collection_dir,
            collection_id=collection_id,
            title=entry.get("title"),
            description=entry.get("description"),
            robot_type=entry.get("robot_type"),
        )

    if collection is None:
        if not include_unavailable:
            return None
        return {
            "id": collection_id,
            "title": entry.get("title") or collection_id,
            "description": entry.get("description"),
            "robot_type": entry.get("robot_type"),
            "package_count": 0,
            "packages": [],
            "kind": "collection",
            "status": "unavailable",
            "expected_path": str(collection_dir),
        }

    collection["id"] = collection_id
    collection["status"] = "available"
    if entry.get("title"):
        collection["title"] = entry["title"]
    if entry.get("description"):
        collection["description"] = entry["description"]
    if entry.get("robot_type"):
        collection["robot_type"] = entry["robot_type"]
    return collection


def _list_registry_collections(registry: dict[str, Any], *, include_unavailable: bool = False) -> list[dict[str, Any]]:
    collections: list[dict[str, Any]] = []
    for entry in registry.get("collections") or []:
        if not isinstance(entry, dict):
            continue
        collection = _load_registry_collection(registry, entry, include_unavailable=include_unavailable)
        if collection is not None:
            collections.append(collection)
    return collections


def _leaf_collection(collection_dir: Path) -> dict[str, Any] | None:
    if not _is_lerobot_root(collection_dir):
        return None
    pkg = _enrich_package(collection_dir, {"id": collection_dir.name, "display_name": collection_dir.name})
    if not pkg.get("loadable"):
        return None
    return {
        "id": collection_dir.name,
        "title": collection_dir.name,
        "description": "LeRobot v3 leaf dataset",
        "package_count": 1,
        "packages": [pkg],
        "kind": "leaf",
    }


def list_collections(*, include_unavailable: bool = False) -> list[dict[str, Any]]:
    registry = _load_collections_registry()
    if registry is not None:
        return _list_registry_collections(registry, include_unavailable=include_unavailable)

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

    collections.sort(key=lambda c: (c.get("title") or c.get("id") or ""))

    allowlist = _collection_allowlist()
    if allowlist is not None:
        collections = [c for c in collections if c.get("id") in allowlist]

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


def allowed_dataset_roots() -> list[Path]:
    """Roots under which POST /api/dataset/load local_path must resolve."""
    roots: set[Path] = set()
    ds_root = datasets_root()
    if ds_root.is_dir():
        roots.add(ds_root)

    active_root = get_active_storage_root()
    if active_root is not None:
        roots.add(active_root)

    registry = _load_collections_registry()
    if registry:
        storage_path = _registry_storage_root(registry).resolve()
        if storage_path.is_dir():
            roots.add(storage_path)

    for collection in list_collections():
        for pkg in collection.get("packages") or []:
            local_path = pkg.get("local_path")
            if not local_path:
                continue
            pkg_root = Path(str(local_path)).expanduser().resolve()
            roots.add(pkg_root)
            if pkg_root.parent.is_dir():
                roots.add(pkg_root.parent)

    return sorted(roots)


def validate_dataset_path(local_path: str | Path) -> Path:
    """Resolve local_path and ensure it lies under an allowed datasets root."""
    root = Path(local_path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Dataset path not found: {root}")

    allowed = allowed_dataset_roots()
    if not allowed:
        return root

    for allowed_root in allowed:
        try:
            root.relative_to(allowed_root.resolve())
            return root
        except ValueError:
            continue

    allowed_text = ", ".join(str(p) for p in allowed)
    raise ValueError(f"Dataset path must be under allowed roots: {allowed_text}")
