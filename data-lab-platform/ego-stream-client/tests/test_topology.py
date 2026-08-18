"""Tests for fleet camera topology loader."""

from __future__ import annotations

import json
from pathlib import Path

from topology import active_topology, all_lerobot_video_keys, load_topology_document, oak_socket_to_lerobot_video

_CONFIG = Path(__file__).resolve().parent.parent / "config"


def test_topology_standard_socket_mapping() -> None:
    doc = load_topology_document(_CONFIG / "camera_topology_standard.json")
    mapping = {
        socket: doc["role_to_lerobot_key"][role]
        for socket, role in doc["socket_to_role"].items()
    }
    assert mapping["CAM_A"] == "observation.images.camera_front_left"
    assert mapping["CAM_B"] == "observation.images.camera_front_right"
    assert mapping["CAM_C"] == "observation.images.camera_rear_left"
    assert mapping["CAM_D"] == "observation.images.camera_rear_right"


def test_topology_standard_viewer_order() -> None:
    doc = load_topology_document(_CONFIG / "camera_topology_standard.json")
    role_to_key = doc["role_to_lerobot_key"]
    keys = tuple(role_to_key[role] for role in doc["viewer_display_order"])
    assert keys == (
        "observation.images.camera_front_left",
        "observation.images.camera_front_right",
        "observation.images.camera_rear_left",
        "observation.images.camera_rear_right",
    )


def test_topology_id_default_standard() -> None:
    doc = active_topology()
    assert doc["topology_id"] == "ego-standard"


def test_topology_json_matches_yaml_contract() -> None:
    cfg = _CONFIG / "camera_topology_standard.json"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert doc["primary_socket"] == "CAM_A"
    assert doc["depth_socket"] == "CAM_C"
