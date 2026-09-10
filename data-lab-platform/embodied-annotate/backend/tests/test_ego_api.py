"""D3 acceptance tests: EGO subtask annotation API + atomic parquet IO."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

import ego_parquet
from app import app

HAND_LEFT = "observation.hand_pose_left"
HAND_RIGHT = "observation.hand_pose_right"


def _write_info(root: Path, features: dict, *, fps: float = 30, total_frames: int = 100) -> None:
    info = {
        "fps": fps,
        "total_frames": total_frames,
        "features": features,
    }
    path = root / "meta" / "info.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info), encoding="utf-8")


def _write_episodes(root: Path, rows: list[dict]) -> None:
    if not rows:
        return
    columns: dict[str, pa.Array] = {}
    for key in rows[0]:
        sample = rows[0][key]
        if key == "tasks":
            columns[key] = pa.array(
                [r[key] if isinstance(r[key], list) else [str(r[key])] for r in rows],
                type=pa.list_(pa.string()),
            )
        elif isinstance(sample, str):
            columns[key] = pa.array([str(r.get(key) or "") for r in rows], type=pa.string())
        elif isinstance(sample, (int, float)):
            columns[key] = pa.array([float(r.get(key) or 0) for r in rows], type=pa.float64())
        else:
            columns[key] = pa.array([r.get(key) for r in rows])
    out = root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    ego_parquet.atomic_write_parquet(pa.table(columns), out)


def _episode_row(
    episode_index: int,
    *,
    status: str = "hand_done",
    ratio: float = 0.72,
    jitter: float = 0.015,
    embodiment: str = "human_demo",
    extended: dict | None = None,
) -> dict:
    ext = {"scene_id": "kitchen"} if extended is None else extended
    return {
        "episode_index": float(episode_index),
        "length": 100.0,
        "task_index": float(episode_index),
        "dataset_from_index": 0.0,
        "dataset_to_index": 100.0,
        "station_id": "ego-001",
        "embodiment": embodiment,
        "task_id": "fold_towel_001",
        "annotation_status": status,
        "pipeline_version": "d1",
        "extended_info": json.dumps(ext, ensure_ascii=False),
        "quality_valid_hand_ratio": ratio,
        "quality_mean_jitter": jitter,
        "tasks": ["fold towel"],
    }


def _write_data_human(root: Path, episode_index: int = 0, n_frames: int = 10) -> None:
    zeros63 = [0.0] * 63
    table = pa.table(
        {
            "episode_index": pa.array([episode_index] * n_frames, type=pa.int64()),
            "frame_index": pa.array(list(range(n_frames)), type=pa.int64()),
            HAND_LEFT: pa.array([zeros63] * n_frames, type=pa.list_(pa.float64())),
            HAND_RIGHT: pa.array([zeros63] * n_frames, type=pa.list_(pa.float64())),
        }
    )
    out = root / "data" / "chunk-000" / "file-000.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    ego_parquet.atomic_write_parquet(table, out)


def _write_data_robot(root: Path, episode_index: int = 0, n_frames: int = 10) -> None:
    table = pa.table(
        {
            "episode_index": pa.array([episode_index] * n_frames, type=pa.int64()),
            "frame_index": pa.array(list(range(n_frames)), type=pa.int64()),
            "observation.ee_pose_left": pa.array([[0.0] * 7] * n_frames, type=pa.list_(pa.float64())),
        }
    )
    out = root / "data" / "chunk-000" / "file-000.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    ego_parquet.atomic_write_parquet(table, out)


def _make_human_dataset(tmp_path: Path, status: str = "hand_done") -> Path:
    root = tmp_path / "human_ds"
    root.mkdir()
    _write_info(
        root,
        {
            "observation.image": {"dtype": "video"},
            HAND_LEFT: {"dtype": "float32", "shape": [63]},
            HAND_RIGHT: {"dtype": "float32", "shape": [63]},
        },
    )
    _write_episodes(root, [_episode_row(0, status=status)])
    _write_data_human(root)
    ego_parquet.ensure_annotations_skeleton(root)
    return root


def _make_robot_dataset(tmp_path: Path) -> Path:
    root = tmp_path / "robot_ds"
    root.mkdir()
    _write_info(
        root,
        {
            "observation.image": {"dtype": "video"},
            "observation.ee_pose_left": {"dtype": "float32", "shape": [7]},
        },
    )
    _write_episodes(
        root,
        [
            _episode_row(
                0,
                status="raw",
                embodiment="robot_teleop_unitree_g1",
                ratio=float("nan"),
                jitter=float("nan"),
            )
        ],
    )
    _write_data_robot(root)
    ego_parquet.ensure_annotations_skeleton(root)
    return root


@pytest.fixture
def client():
    return TestClient(app)


class TestEgoLoadSave:
    def test_human_hand_done_load_and_save(self, client, tmp_path):
        root = _make_human_dataset(tmp_path, status="hand_done")
        ep_path = ego_parquet.episodes_parquet_path(root)
        before_ep = pq.read_table(ep_path).to_pydict()

        res = client.get("/api/ego/load", params={"datasetPath": str(root), "episodeIndex": 0})
        assert res.status_code == 200
        body = res.json()
        assert body["episode_meta"]["annotation_status"] == "hand_done"
        assert body["episode_meta"]["quality_valid_hand_ratio"] == pytest.approx(0.72)
        assert HAND_LEFT in body["features"]
        assert body["total_frames"] == 100

        hp = client.get("/api/ego/hand_poses", params={"datasetPath": str(root), "episodeIndex": 0})
        assert hp.status_code == 200
        assert len(hp.json()["poses"]) == 10

        save = client.post(
            "/api/ego/save",
            json={
                "dataset_path": str(root),
                "episode_index": 0,
                "subtasks": [
                    {"start_frame": 0, "end_frame": 49, "subtask_index": 0, "subtask_name": "拿起毛巾"},
                    {"start_frame": 50, "end_frame": 99, "subtask_index": 1, "subtask_name": "对折一次"},
                ],
            },
        )
        assert save.status_code == 200
        assert save.json() == {"success": True, "updated_rows": 2}

        ann = pq.read_table(ego_parquet.annotations_parquet_path(root))
        assert ann.num_rows == 2
        assert set(ann.column_names) == {"episode_index", "frame_index", "subtask_index", "subtask_name"}
        assert ann["episode_index"].to_pylist() == [0, 0]

        after_ep = pq.read_table(ep_path).to_pydict()
        assert after_ep["annotation_status"][0] == "subtask_done"
        assert after_ep["quality_valid_hand_ratio"][0] == before_ep["quality_valid_hand_ratio"][0]
        assert after_ep["quality_mean_jitter"][0] == before_ep["quality_mean_jitter"][0]
        ext = json.loads(after_ep["extended_info"][0])
        assert ext["scene_id"] == "kitchen"
        assert ext.get("last_subtask_save") is True

    def test_robot_no_hand_features(self, client, tmp_path):
        root = _make_robot_dataset(tmp_path)
        res = client.get("/api/ego/load", params={"datasetPath": str(root), "episodeIndex": 0})
        assert res.status_code == 200
        body = res.json()
        assert HAND_LEFT not in body["features"]
        assert "observation.ee_pose_left" in body["features"]

        hp = client.get("/api/ego/hand_poses", params={"datasetPath": str(root), "episodeIndex": 0})
        assert hp.status_code == 200
        assert hp.json()["poses"] == []

        save = client.post(
            "/api/ego/save",
            json={
                "dataset_path": str(root),
                "episode_index": 0,
                "subtasks": [
                    {"start_frame": 0, "end_frame": 9, "subtask_index": 0, "subtask_name": "approach"},
                ],
            },
        )
        assert save.status_code == 200
        assert pq.read_table(ego_parquet.annotations_parquet_path(root)).num_rows == 1

    def test_failed_episode_rejected(self, client, tmp_path):
        root = _make_human_dataset(tmp_path, status="failed")
        ann_path = ego_parquet.annotations_parquet_path(root)
        ep_path = ego_parquet.episodes_parquet_path(root)
        ann_before = ann_path.read_bytes()
        ep_before = ep_path.read_bytes()

        save = client.post(
            "/api/ego/save",
            json={
                "dataset_path": str(root),
                "episode_index": 0,
                "subtasks": [
                    {"start_frame": 0, "end_frame": 10, "subtask_index": 0, "subtask_name": "x"},
                ],
            },
        )
        assert save.status_code == 400
        assert "failed" in save.json()["detail"].lower()
        assert ann_path.read_bytes() == ann_before
        assert ep_path.read_bytes() == ep_before

    def test_subtask_done_overwrite_keeps_status(self, client, tmp_path):
        root = _make_human_dataset(tmp_path, status="subtask_done")
        client.post(
            "/api/ego/save",
            json={
                "dataset_path": str(root),
                "episode_index": 0,
                "subtasks": [
                    {"start_frame": 0, "end_frame": 40, "subtask_index": 0, "subtask_name": "old"},
                ],
            },
        )
        save = client.post(
            "/api/ego/save",
            json={
                "dataset_path": str(root),
                "episode_index": 0,
                "subtasks": [
                    {"start_frame": 0, "end_frame": 60, "subtask_index": 0, "subtask_name": "new"},
                ],
            },
        )
        assert save.status_code == 200
        ann = pq.read_table(ego_parquet.annotations_parquet_path(root))
        assert ann.num_rows == 1
        assert ann["subtask_name"][0].as_py() == "new"
        ep = pq.read_table(ego_parquet.episodes_parquet_path(root))
        assert ep["annotation_status"][0].as_py() == "subtask_done"

    def test_pusht_route_unchanged(self, client):
        res = client.post("/api/dataset/load", json={"source": "local", "local_path": "/nonexistent"})
        assert res.status_code == 404

    def test_atomic_write_failure_leaves_original(self, tmp_path):
        root = _make_human_dataset(tmp_path)
        ann_path = ego_parquet.annotations_parquet_path(root)
        original = ann_path.read_bytes()

        real_atomic = ego_parquet.atomic_write_parquet

        def flaky_atomic(table, path, *, expected_rows=None):
            if path.name == "annotations.parquet":
                raise OSError("simulated interrupt")
            return real_atomic(table, path, expected_rows=expected_rows)

        with mock.patch.object(ego_parquet, "atomic_write_parquet", side_effect=flaky_atomic):
            with pytest.raises(OSError):
                ego_parquet.write_annotations_for_episode(
                    root,
                    0,
                    [{"start_frame": 0, "end_frame": 1, "subtask_index": 0, "subtask_name": "a"}],
                )
        assert ann_path.read_bytes() == original
        assert not ann_path.with_suffix(".parquet.tmp").exists()

    def test_multi_episode_annotation_isolation(self, client, tmp_path):
        root = tmp_path / "multi_ds"
        root.mkdir()
        _write_info(
            root,
            {
                "observation.image": {"dtype": "video"},
                HAND_LEFT: {"dtype": "float32", "shape": [63]},
            },
        )
        _write_episodes(
            root,
            [
                _episode_row(0, status="hand_done", ratio=0.5, jitter=0.01),
                _episode_row(1, status="hand_done", ratio=0.9, jitter=0.02),
            ],
        )
        ego_parquet.ensure_annotations_skeleton(root)
        # Pre-annotate episode 1
        ego_parquet.write_annotations_for_episode(
            root,
            1,
            [{"start_frame": 0, "end_frame": 10, "subtask_index": 0, "subtask_name": "ep1-only"}],
        )

        save = client.post(
            "/api/ego/save",
            json={
                "dataset_path": str(root),
                "episode_index": 0,
                "subtasks": [
                    {"start_frame": 0, "end_frame": 20, "subtask_index": 0, "subtask_name": "ep0-task"},
                ],
            },
        )
        assert save.status_code == 200

        ann = pq.read_table(ego_parquet.annotations_parquet_path(root))
        ep0_rows = [i for i in range(ann.num_rows) if ann["episode_index"][i].as_py() == 0]
        ep1_rows = [i for i in range(ann.num_rows) if ann["episode_index"][i].as_py() == 1]
        assert len(ep0_rows) == 1
        assert len(ep1_rows) == 1
        assert ann["subtask_name"][ep1_rows[0]].as_py() == "ep1-only"

        ep = pq.read_table(ego_parquet.episodes_parquet_path(root))
        assert ep["annotation_status"][0].as_py() == "subtask_done"
        assert ep["annotation_status"][1].as_py() == "hand_done"
        assert ep["quality_valid_hand_ratio"][1].as_py() == pytest.approx(0.9)

    def test_load_404_and_400(self, client, tmp_path):
        missing = tmp_path / "missing"
        assert client.get("/api/ego/load", params={"datasetPath": str(missing)}).status_code == 404

        root = _make_human_dataset(tmp_path)
        assert client.get("/api/ego/load", params={"datasetPath": str(root), "episodeIndex": 99}).status_code == 400


class TestExtendedInfoMerge:
    def test_merge_preserves_existing_keys(self):
        merged = ego_parquet.merge_extended_info('{"scene_id":"a","foo":1}', {"last_subtask_save": True})
        obj = json.loads(merged)
        assert obj["scene_id"] == "a"
        assert obj["foo"] == 1
        assert obj["last_subtask_save"] is True
