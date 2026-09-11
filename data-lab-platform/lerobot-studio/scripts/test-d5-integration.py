#!/usr/bin/env python3
"""D5: format compliance, human chain, pusht regression."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from argparse import Namespace
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DATA_LAB = ROOT.parent
UNITREE = DATA_LAB / "stream-data" / "unitree-g1-teleop-001"
PUSHT = DATA_LAB.parent / "data-storage" / "embodied-annotate" / "datasets" / "pusht"
EGO_BACKEND = DATA_LAB / "embodied-annotate" / "backend"
EGO_HAND = DATA_LAB.parent.parent / "ego-hand-pipeline"

EPISODE_META_COLS = {
    "station_id",
    "embodiment",
    "task_id",
    "annotation_status",
    "pipeline_version",
    "extended_info",
    "quality_valid_hand_ratio",
    "quality_mean_jitter",
}
DATA_FORBIDDEN_COLS = EPISODE_META_COLS
HAND_KEYS = {
    "observation.hand_pose_left",
    "observation.hand_pose_right",
    "observation.hand_conf_left",
    "observation.hand_conf_right",
}
VIDEO_KEY = "observation.images.camera_front_left"


def ok(msg: str) -> None:
    print(f"✓ {msg}")


def fail(msg: str) -> None:
    raise AssertionError(msg)


def validate_lerobot_v3_layout(root: Path) -> None:
    assert (root / "meta" / "info.json").is_file(), f"missing info.json: {root}"
    info = json.loads((root / "meta" / "info.json").read_text(encoding="utf-8"))
    assert info.get("codebase_version", "").startswith("v3"), "codebase_version must be v3"
    assert "features" in info and isinstance(info["features"], dict), "features dict required"
    assert "data_path" in info and "video_path" in info, "LeRobot v3 path templates"
    ok(f"LeRobot v3 layout: {root.name}")


def validate_episodes_metadata(root: Path) -> None:
    ep_path = root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    if not ep_path.is_file():
        fail(f"missing episodes parquet: {ep_path}")
    table = pq.read_table(ep_path)
    for col in ("station_id", "embodiment", "annotation_status", "extended_info"):
        assert col in table.column_names, f"episodes missing {col}"
    ok(f"episodes business metadata: {root.name}")


def validate_data_no_mgmt_cols(root: Path) -> None:
    data_path = root / "data" / "chunk-000" / "file-000.parquet"
    if not data_path.is_file():
        ok(f"data.parquet absent (minimal skeleton): {root.name}")
        return
    cols = set(pq.read_schema(data_path).names)
    overlap = cols & DATA_FORBIDDEN_COLS
    assert not overlap, f"data parquet must not contain mgmt cols: {overlap}"
    ok(f"data parquet free of mgmt cols: {root.name}")


def validate_unitree_no_hand_features(root: Path) -> None:
    info = json.loads((root / "meta" / "info.json").read_text(encoding="utf-8"))
    feats = info.get("features") or {}
    for key in HAND_KEYS:
        assert key not in feats, f"unitree must not register {key}"
    for key in (
        "observation.joint_pos",
        "observation.joint_vel",
        "observation.ee_pose_left",
        "observation.ee_pose_right",
    ):
        assert key in feats, f"unitree missing {key}"
    ok("unitree on-demand features, no hand_pose")


def _make_human_dataset(tmp_path: Path) -> Path:
    root = tmp_path / "human_ds"
    root.mkdir(parents=True)
    features = {
        "observation.state": {"dtype": "float32", "shape": [6]},
        "observation.pose": {"dtype": "float32", "shape": [7]},
        "observation.hands": {"dtype": "float32", "shape": [63]},
        "action": {"dtype": "float32", "shape": [1]},
        VIDEO_KEY: {"dtype": "video", "shape": [48, 64, 3], "info": {"video.fps": 20}},
        "observation.hand_pose_left": {"dtype": "float32", "shape": [63]},
        "observation.hand_pose_right": {"dtype": "float32", "shape": [63]},
        "observation.hand_conf_left": {"dtype": "float32", "shape": [1]},
        "observation.hand_conf_right": {"dtype": "float32", "shape": [1]},
    }
    (root / "meta").mkdir(parents=True)
    (root / "meta" / "info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "fps": 20,
                "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
                "features": features,
            }
        ),
        encoding="utf-8",
    )
    ep_path = root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    ep_path.parent.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "episode_index": pa.array([0.0], type=pa.float64()),
                "length": pa.array([3.0], type=pa.float64()),
                "task_index": pa.array([0.0], type=pa.float64()),
                "dataset_from_index": pa.array([0.0], type=pa.float64()),
                "dataset_to_index": pa.array([3.0], type=pa.float64()),
                "station_id": pa.array(["ego-001"], type=pa.string()),
                "embodiment": pa.array(["human_demo"], type=pa.string()),
                "task_id": pa.array(["fold_towel"], type=pa.string()),
                "annotation_status": pa.array(["raw"], type=pa.string()),
                "pipeline_version": pa.array(["1.0.0"], type=pa.string()),
                "extended_info": pa.array(["{}"], type=pa.string()),
                "quality_valid_hand_ratio": pa.array([float("nan")], type=pa.float64()),
                "quality_mean_jitter": pa.array([float("nan")], type=pa.float64()),
                f"videos/{VIDEO_KEY}/chunk_index": pa.array([0.0], type=pa.float64()),
                f"videos/{VIDEO_KEY}/file_index": pa.array([0.0], type=pa.float64()),
                f"videos/{VIDEO_KEY}/from_timestamp": pa.array([0.0], type=pa.float64()),
                f"videos/{VIDEO_KEY}/to_timestamp": pa.array([0.15], type=pa.float64()),
                "tasks": pa.array([["fold_towel · 3f"]], type=pa.list_(pa.string())),
            }
        ),
        ep_path,
    )
    data_path = root / "data" / "chunk-000" / "file-000.parquet"
    data_path.parent.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "frame_index": pa.array([0, 1, 2], type=pa.int64()),
                "episode_index": pa.array([0, 0, 0], type=pa.int64()),
                "index": pa.array([0, 1, 2], type=pa.int64()),
                "task_index": pa.array([0, 0, 0], type=pa.int64()),
                "timestamp": pa.array([0.0, 0.05, 0.1], type=pa.float64()),
                "observation.state": pa.array([[0.0] * 6] * 3, type=pa.list_(pa.float32(), 6)),
                "observation.pose": pa.array([[0, 0, 0, 0, 0, 0, 1.0]] * 3, type=pa.list_(pa.float32(), 7)),
                "observation.hands": pa.array([[0.0] * 63] * 3, type=pa.list_(pa.float32(), 63)),
                "action": pa.array([0.0, 0.0, 0.0], type=pa.float32()),
            }
        ),
        data_path,
    )
    und_dir = root / "offline" / "episode_000000" / "rectified"
    und_dir.mkdir(parents=True)
    und_path = und_dir / "camera_front_left_rectified.mp4"
    ff = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x48:d=0.15",
            "-vf",
            "fps=20",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(und_path),
        ],
        capture_output=True,
        text=True,
    )
    if ff.returncode != 0:
        und_path.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00")
    return root


def human_full_chain() -> None:
    sys.path.insert(0, str(EGO_HAND / "src"))
    sys.path.insert(0, str(EGO_BACKEND))
    from ego_hand_pipeline.lerobot_writeback_io import (
        HAND_POSE_LEFT,
        get_episode_annotation_status,
    )
    from fastapi.testclient import TestClient
    from app import app

    hand_py = EGO_HAND / ".venv" / "bin" / "python"
    if not hand_py.is_file():
        hand_py = Path(sys.executable)

    with tempfile.TemporaryDirectory(prefix="d5_human_") as td:
        root = _make_human_dataset(Path(td))
        wb = subprocess.run(
            [
                str(hand_py),
                str(EGO_HAND / "src" / "ego_hand_pipeline" / "cli" / "lerobot_writeback.py"),
                "--dataset-path",
                str(root),
                "--episode-index",
                "0",
                "--hamer-backend",
                "stub",
                "--detect-backend",
                "center",
            ],
            capture_output=True,
            text=True,
            env={**dict(os.environ), "PYTHONPATH": str(EGO_HAND / "src")},
        )
        if wb.returncode != 0:
            fail(f"writeback failed: {wb.stderr or wb.stdout}")
        assert get_episode_annotation_status(root, 0) == "hand_done"

        ep = pq.read_table(root / "meta" / "episodes" / "chunk-000" / "file-000.parquet")
        ratio = ep["quality_valid_hand_ratio"][0].as_py()
        jitter = ep["quality_mean_jitter"][0].as_py()
        assert ratio == ratio and not (isinstance(ratio, float) and math.isnan(ratio))
        assert jitter == jitter and not (isinstance(jitter, float) and math.isnan(jitter))

        table = pq.read_table(root / "data" / "chunk-000" / "file-000.parquet")
        assert HAND_POSE_LEFT in table.column_names
        left = np.asarray(table[HAND_POSE_LEFT][0].as_py(), dtype=np.float32)
        assert left.shape == (63,)

        client = TestClient(app)
        load = client.get("/api/ego/load", params={"datasetPath": str(root), "episodeIndex": 0})
        assert load.status_code == 200, load.text
        body = load.json()
        assert body["episode_meta"]["annotation_status"] == "hand_done"
        assert HAND_POSE_LEFT in body["features"]

        hp = client.get("/api/ego/hand_poses", params={"datasetPath": str(root), "episodeIndex": 0})
        assert hp.status_code == 200
        assert len(hp.json()["poses"]) > 0

        save = client.post(
            "/api/ego/save",
            json={
                "dataset_path": str(root),
                "episode_index": 0,
                "subtasks": [
                    {"start_frame": 0, "end_frame": 1, "subtask_index": 0, "subtask_name": "pick"},
                ],
            },
        )
        assert save.status_code == 200, save.text
        assert get_episode_annotation_status(root, 0) == "subtask_done"

    ok("human chain raw → hand_done → subtask_done + ego load/hand_poses/save")


def pusht_regression() -> None:
    if not PUSHT.is_dir():
        print("⊘ skip pusht (dataset not present)")
        return
    before = (PUSHT / "meta" / "info.json").read_bytes()
    sys.path.insert(0, str(EGO_BACKEND))
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app)
    load = client.post(
        "/api/dataset/load",
        json={"source": "local", "local_path": str(PUSHT)},
    )
    assert load.status_code == 200, load.text

    info = client.get("/api/dataset/info")
    assert info.status_code == 200
    episodes = load.json().get("episodes") or []
    assert len(episodes) > 0, "pusht must expose episodes"

    ep0 = episodes[0]["episode_index"]
    ann = client.get(f"/api/episodes/{ep0}/annotations")
    assert ann.status_code == 200

    timing = client.get(f"/api/episodes/{ep0}/video_timing")
    assert timing.status_code == 200

    ego = client.get("/api/ego/load", params={"datasetPath": str(PUSHT), "episodeIndex": ep0})
    assert ego.status_code in (200, 404), "ego route must not crash pusht stack"

    after = (PUSHT / "meta" / "info.json").read_bytes()
    assert before == after, "pusht info.json must not be modified"
    ok("pusht demo routes + dataset untouched")


def main() -> int:
    checks = [
        ("unitree-fixture", lambda: (
            validate_lerobot_v3_layout(UNITREE),
            validate_episodes_metadata(UNITREE),
            validate_unitree_no_hand_features(UNITREE),
            validate_data_no_mgmt_cols(UNITREE),
        )),
        ("human-chain", human_full_chain),
        ("pusht-regression", pusht_regression),
    ]
    for name, fn in checks:
        try:
            fn()
        except Exception as exc:
            print(f"✗ {name}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
