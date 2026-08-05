"""Runtime storage root selection for QC (no docker-compose edits per disk swap)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

HOST_MEDIA_ROOT = Path(os.environ.get("LEROBOT_QC_HOST_MEDIA", "/host-media")).expanduser()
HOST_MEDIA_HOST_PREFIX = os.environ.get("LEROBOT_QC_HOST_MEDIA_HOST_PREFIX", "/media/user01").rstrip("/")
LEGACY_BOOKDUO_ROOT = Path("/data/bookduo")
STORAGE_CONFIG_PATH = Path(
    os.environ.get("LEROBOT_QC_STORAGE_CONFIG", "/data/qc-sidecar/storage.json"),
).expanduser()


def _read_config() -> dict[str, Any]:
    if not STORAGE_CONFIG_PATH.is_file():
        return {}
    try:
        payload = json.loads(STORAGE_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_config(payload: dict[str, Any]) -> None:
    STORAGE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORAGE_CONFIG_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _is_bookduo_dir_name(name: str) -> bool:
    return name.lower().startswith("bookduo")


def _iter_host_bookduo_dirs() -> list[Path]:
    if not HOST_MEDIA_ROOT.is_dir():
        return []
    volumes: list[Path] = []
    try:
        for entry in sorted(HOST_MEDIA_ROOT.iterdir()):
            if entry.is_dir() and _is_bookduo_dir_name(entry.name) and not entry.name.startswith("."):
                volumes.append(entry.resolve())
    except OSError:
        return []
    return volumes


def normalize_container_storage_path(path: str | Path) -> Path:
    """Map legacy /data/bookduo paths to the host-media BookDuo mount."""
    resolved = Path(path).expanduser().resolve()
    legacy = LEGACY_BOOKDUO_ROOT.resolve()
    try:
        relative = resolved.relative_to(legacy)
    except ValueError:
        return resolved

    host_root = _iter_host_bookduo_dirs()
    if not host_root:
        return resolved
    primary = host_root[0]
    if relative == Path("."):
        return primary
    return (primary / relative).resolve()


def _is_bookduo_volume_path(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(HOST_MEDIA_ROOT.resolve())
    except ValueError:
        return False
    return bool(relative.parts) and _is_bookduo_dir_name(relative.parts[0])


def _allowed_roots() -> list[Path]:
    return _iter_host_bookduo_dirs()


def _is_under_allowed_root(path: Path) -> bool:
    return _is_bookduo_volume_path(path)


def container_to_host_path(container_path: str | Path) -> str:
    resolved = normalize_container_storage_path(container_path)
    host_media = HOST_MEDIA_ROOT.resolve()
    try:
        relative = resolved.relative_to(host_media)
        return f"{HOST_MEDIA_HOST_PREFIX}/{relative.as_posix()}"
    except ValueError:
        return str(resolved)


def _child_dir_count(path: Path) -> int:
    if not path.is_dir():
        return 0
    try:
        return sum(1 for entry in path.iterdir() if entry.is_dir() and not entry.name.startswith("."))
    except OSError:
        return 0


def _volume_status(path: Path) -> str:
    if not path.is_dir():
        return "offline"
    try:
        children = [entry for entry in path.iterdir() if not entry.name.startswith(".")]
    except OSError:
        return "offline"
    if not children:
        return "offline"
    return "available"


def _volume_info(path: Path, *, saved: bool = False) -> dict[str, Any]:
    status = _volume_status(path)
    return {
        "id": path.name,
        "name": path.name,
        "container_path": str(path.resolve()),
        "host_path": container_to_host_path(path),
        "status": status,
        "saved": saved,
        "item_count": _child_dir_count(path),
    }


def list_detected_volumes() -> list[dict[str, Any]]:
    config = _read_config()
    saved_path = str(config.get("active_root") or "").strip()
    saved_resolved = normalize_container_storage_path(saved_path) if saved_path else None

    volumes: dict[str, dict[str, Any]] = {}

    for entry in _iter_host_bookduo_dirs():
        volumes[str(entry)] = _volume_info(
            entry,
            saved=saved_resolved is not None and entry == saved_resolved,
        )

    if saved_resolved and _is_bookduo_volume_path(saved_resolved) and str(saved_resolved) not in volumes:
        volumes[str(saved_resolved)] = _volume_info(saved_resolved, saved=True)

    return sorted(
        volumes.values(),
        key=lambda item: (item.get("status") != "available", item.get("name") or ""),
    )


def get_active_storage_root() -> Path | None:
    config = _read_config()
    saved = str(config.get("active_root") or "").strip()
    if saved:
        candidate = normalize_container_storage_path(saved)
        if candidate.is_dir() and _volume_status(candidate) == "available":
            return candidate.resolve()

    for volume in list_detected_volumes():
        if volume.get("status") == "available":
            return Path(str(volume["container_path"])).resolve()

    return None


def get_storage_status() -> dict[str, Any]:
    active = get_active_storage_root()
    config = _read_config()
    saved = str(config.get("active_root") or "").strip()
    saved_resolved = normalize_container_storage_path(saved) if saved else None
    saved_exists = bool(saved_resolved and saved_resolved.is_dir())
    saved_available = bool(saved_resolved and _volume_status(saved_resolved) == "available")
    return {
        "active_root": str(active) if active else None,
        "active_host_path": container_to_host_path(active) if active else None,
        "saved_root": str(saved_resolved) if saved_resolved else saved or None,
        "saved_host_path": container_to_host_path(saved_resolved) if saved_exists and saved_resolved else saved or None,
        "saved_available": saved_available,
        "host_media_root": str(HOST_MEDIA_ROOT),
        "host_media_host_prefix": HOST_MEDIA_HOST_PREFIX,
        "volumes": list_detected_volumes(),
    }


def activate_storage_root(container_path: str) -> dict[str, Any]:
    candidate = normalize_container_storage_path(container_path).resolve()
    if not candidate.is_dir():
        raise ValueError(f"Storage path not found: {container_path}")
    if not _is_under_allowed_root(candidate):
        allowed = ", ".join(str(root) for root in _allowed_roots()) or str(HOST_MEDIA_ROOT / "BookDuo_*")
        raise ValueError(f"Storage path must be a detected external volume under: {allowed}")

    status = _volume_status(candidate)
    if status != "available":
        host_path = container_to_host_path(candidate)
        raise ValueError(f"External storage is offline or empty: {host_path}")

    config = _read_config()
    config["active_root"] = str(candidate)
    config["active_host_path"] = container_to_host_path(candidate)
    _write_config(config)

    return {
        "active_root": str(candidate),
        "active_host_path": config["active_host_path"],
        "status": status,
    }
