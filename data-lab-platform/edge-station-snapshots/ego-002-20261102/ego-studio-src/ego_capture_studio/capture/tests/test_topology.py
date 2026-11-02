"""Tests for fleet camera topology loader."""

from __future__ import annotations

import json
from pathlib import Path

from topology import active_topology, all_lerobot_video_keys, oak_socket_to_lerobot_video


def test_topology_standard_socket_mapping() -> None:
    mapping = oak_socket_to_lerobot_video()
    assert mapping["CAM_A"] == "observation.images.camera_front_left"
    assert mapping["CAM_B"] == "observation.images.camera_front_right"
    assert mapping["CAM_C"] == "observation.images.camera_rear_left"
    assert mapping["CAM_D"] == "observation.images.camera_rear_right"


def test_topology_standard_viewer_order() -> None:
    keys = all_lerobot_video_keys()
    assert keys == (
        "observation.images.camera_front_left",
        "observation.images.camera_front_right",
        "observation.images.camera_rear_left",
        "observation.images.camera_rear_right",
    )


def test_topology_id() -> None:
    doc = active_topology()
    assert doc["topology_id"] == "ego-standard"


def test_topology_json_matches_yaml_contract(tmp_path: Path) -> None:
    cfg = Path(__file__).resolve().parent / "config" / "camera_topology_standard.json"
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert doc["primary_socket"] == "CAM_A"
    assert doc["depth_socket"] == "CAM_C"


def test_topology_ego_001_physical_socket_mapping(monkeypatch) -> None:
    cfg = Path(__file__).resolve().parent / "config" / "camera_topology_ego-001.json"
    monkeypatch.setenv("EGO_TOPOLOGY_FILE", str(cfg))
    active_topology.cache_clear()
    mapping = oak_socket_to_lerobot_video()
    assert mapping["CAM_A"] == "observation.images.camera_front_left"
    assert mapping["CAM_B"] == "observation.images.camera_rear_left"
    assert mapping["CAM_C"] == "observation.images.camera_rear_right"
    assert mapping["CAM_D"] == "observation.images.camera_front_right"
    assert active_topology()["topology_id"] == "ego-001-physical"
    active_topology.cache_clear()
