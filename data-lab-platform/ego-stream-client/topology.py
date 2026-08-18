"""Load fleet camera topology manifests (socket -> role -> LeRobot key)."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(__file__).resolve().parent / "config"
_DEFAULT_TOPOLOGY_JSON = _CONFIG_DIR / "camera_topology_standard.json"


def _topology_path() -> Path:
    raw = os.environ.get("EGO_TOPOLOGY_FILE", "").strip()
    if raw:
        path = Path(raw).expanduser()
        if path.suffix.lower() in {".yaml", ".yml"}:
            sibling = path.with_suffix(".json")
            if sibling.is_file():
                return sibling
        return path
    return _DEFAULT_TOPOLOGY_JSON


def load_topology_document(path: Path | None = None) -> dict[str, Any]:
    path = path or _topology_path()
    if not path.is_file():
        raise FileNotFoundError(f"camera topology not found: {path}")
    if path.suffix.lower() in {".yaml", ".yml"}:
        json_path = path.with_suffix(".json")
        if json_path.is_file():
            path = json_path
        else:
            raise FileNotFoundError(
                f"YAML topology requires sibling JSON for runtime load: {json_path}"
            )
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_topology(doc: dict[str, Any]) -> None:
    required = ("topology_id", "socket_to_role", "role_to_lerobot_key", "primary_socket")
    missing = [key for key in required if key not in doc]
    if missing:
        raise ValueError(f"topology missing keys: {missing}")
    socket_to_role = doc["socket_to_role"]
    role_to_key = doc["role_to_lerobot_key"]
    if not isinstance(socket_to_role, dict) or not socket_to_role:
        raise ValueError("topology.socket_to_role must be a non-empty object")
    if not isinstance(role_to_key, dict) or not role_to_key:
        raise ValueError("topology.role_to_lerobot_key must be a non-empty object")
    for socket, role in socket_to_role.items():
        if role not in role_to_key:
            raise ValueError(f"topology role {role!r} for {socket} has no lerobot key")


@lru_cache(maxsize=4)
def active_topology(path: str | None = None) -> dict[str, Any]:
    doc = load_topology_document(Path(path) if path else None)
    _validate_topology(doc)
    return doc


def topology_snapshot() -> dict[str, Any]:
    """Compact block stored in session camera_intrinsics.json."""
    doc = active_topology()
    return {
        "topology_id": doc["topology_id"],
        "version": int(doc.get("version") or 1),
        "board_profile": doc.get("board_profile"),
        "primary_socket": doc["primary_socket"],
        "depth_socket": doc.get("depth_socket"),
        "head_right_socket": doc.get("head_right_socket"),
        "socket_to_role": dict(doc["socket_to_role"]),
        "role_to_lerobot_key": dict(doc["role_to_lerobot_key"]),
        "viewer_display_order": list(doc.get("viewer_display_order") or []),
    }


def oak_socket_to_lerobot_video() -> dict[str, str]:
    doc = active_topology()
    socket_to_role = doc["socket_to_role"]
    role_to_key = doc["role_to_lerobot_key"]
    return {socket: role_to_key[role] for socket, role in socket_to_role.items()}


def lerobot_video_to_oak_socket() -> dict[str, str]:
    mapping = oak_socket_to_lerobot_video()
    return {value: key for key, value in mapping.items()}


def all_lerobot_video_keys() -> tuple[str, ...]:
    doc = active_topology()
    order = doc.get("viewer_display_order")
    role_to_key = doc["role_to_lerobot_key"]
    if isinstance(order, list) and order:
        keys: list[str] = []
        for role in order:
            key = role_to_key.get(role)
            if key and key not in keys:
                keys.append(key)
        if keys:
            return tuple(keys)
    return tuple(role_to_key[role] for role in doc["socket_to_role"].values())


def primary_oak_socket() -> str:
    return str(active_topology()["primary_socket"])


def primary_lerobot_video_key() -> str:
    return oak_socket_to_lerobot_video()[primary_oak_socket()]


def depth_oak_socket() -> str:
    doc = active_topology()
    return str(doc.get("depth_socket") or "CAM_B")


def head_right_oak_socket() -> str:
    doc = active_topology()
    return str(doc.get("head_right_socket") or "CAM_D")


def strict_causal_oak_sockets() -> frozenset[str]:
    doc = active_topology()
    sync = doc.get("strict_sync") or {}
    sockets = sync.get("causal_sockets") or []
    return frozenset(str(s).strip() for s in sockets if str(s).strip())
