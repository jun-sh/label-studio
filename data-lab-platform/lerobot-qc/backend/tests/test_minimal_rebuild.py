"""Integration tests for minimal-derivation rebuild."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import rebuild_service
from dataset_manager import load_local_dataset
from qc_store import QcStore


@pytest.fixture()
def minimal_dataset(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "source_ds"
    sidecar_base = tmp_path / "sidecar"
    (root / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (root / "videos" / "observation.images.top" / "chunk-000").mkdir(parents=True)
    (root / "videos" / "observation.images.depth" / "chunk-000").mkdir(parents=True)
    (root / "data" / "chunk-000").mkdir(parents=True)

    (root / "meta" / "info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "robot_type": "test_robot",
                "total_episodes": 3,
                "total_frames": 15,
                "fps": 5,
                "chunks_size": 1000,
                "splits": {"train": "0:3"},
                "features": {
                    "observation.images.top": {
                        "dtype": "video",
                        "shape": [3, 64, 64],
                        "info": {"video.is_depth_map": False},
                    },
                    "observation.images.depth": {
                        "dtype": "video",
                        "shape": [1, 64, 64],
                        "info": {
                            "video.is_depth_map": True,
                            "depth.video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mkv",
                        },
                    },
                    "action": {"dtype": "float32", "shape": [2]},
                    "observation.state": {"dtype": "float32", "shape": [2]},
                    "timestamp": {"dtype": "float32", "shape": [1]},
                    "frame_index": {"dtype": "int64", "shape": [1]},
                    "episode_index": {"dtype": "int64", "shape": [1]},
                    "index": {"dtype": "int64", "shape": [1]},
                    "task_index": {"dtype": "int64", "shape": [1]},
                },
            }
        ),
        encoding="utf-8",
    )

    episodes = pd.DataFrame(
        [
            {
                "episode_index": 0,
                "tasks": np.array(["pick the cube"], dtype=object),
                "length": 5,
                "dataset_from_index": 0,
                "dataset_to_index": 5,
                "task_index": 0,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 1.0,
                "videos/observation.images.depth/chunk_index": 0,
                "videos/observation.images.depth/file_index": 0,
                "videos/observation.images.depth/from_timestamp": 0.0,
                "videos/observation.images.depth/to_timestamp": 1.0,
                "stats/action/mean": np.array([0.1, 0.2]),
            },
            {
                "episode_index": 1,
                "tasks": np.array(["pick the cube"], dtype=object),
                "length": 5,
                "dataset_from_index": 5,
                "dataset_to_index": 10,
                "task_index": 0,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 1.0,
                "videos/observation.images.top/to_timestamp": 2.0,
                "videos/observation.images.depth/chunk_index": 0,
                "videos/observation.images.depth/file_index": 0,
                "videos/observation.images.depth/from_timestamp": 1.0,
                "videos/observation.images.depth/to_timestamp": 2.0,
                "stats/action/mean": np.array([0.3, 0.4]),
            },
            {
                "episode_index": 2,
                "tasks": np.array(["stack the cup"], dtype=object),
                "length": 5,
                "dataset_from_index": 10,
                "dataset_to_index": 15,
                "task_index": 1,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 2.0,
                "videos/observation.images.top/to_timestamp": 3.0,
                "videos/observation.images.depth/chunk_index": 0,
                "videos/observation.images.depth/file_index": 0,
                "videos/observation.images.depth/from_timestamp": 2.0,
                "videos/observation.images.depth/to_timestamp": 3.0,
                "stats/action/mean": np.array([0.5, 0.6]),
            },
        ]
    )
    pq.write_table(
        pa.Table.from_pandas(episodes, preserve_index=False),
        root / "meta" / "episodes" / "chunk-000" / "file-000.parquet",
    )
    tasks = pd.DataFrame(
        [
            {"task_index": 0, "task": "pick the cube"},
            {"task_index": 1, "task": "stack the cup"},
        ]
    )
    pq.write_table(pa.Table.from_pandas(tasks, preserve_index=False), root / "meta" / "tasks.parquet")

    frames = pd.DataFrame(
        {
            "episode_index": [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2],
            "frame_index": [0, 1, 2, 3, 4] * 3,
            "index": list(range(15)),
            "timestamp": np.arange(15, dtype=np.float32) / 5.0,
            "task_index": [0] * 10 + [1] * 5,
            "action": [[0.0, 0.1] for _ in range(15)],
            "observation.state": [[1.0, 2.0] for _ in range(15)],
        }
    )
    pq.write_table(
        pa.Table.from_pandas(frames, preserve_index=False),
        root / "data" / "chunk-000" / "file-000.parquet",
    )
    (root / "meta" / "stats.json").write_text(
        json.dumps(
            {
                "action": {"count": [15], "mean": [0.0, 0.0]},
                "timestamp": {"count": [15], "mean": [1.0]},
            }
        ),
        encoding="utf-8",
    )
    (root / "videos" / "observation.images.top" / "chunk-000" / "file-000.mp4").write_bytes(b"shared-rgb")
    (root / "videos" / "observation.images.depth" / "chunk-000" / "file-000.mkv").write_bytes(b"shared-depth")
    return root, sidecar_base


def _prepare_store(dataset_root: Path, sidecar_base: Path) -> QcStore:
    store = QcStore.open(dataset_root, sidecar_base, operator_id="tester")
    store.set_review(1, "rejected", reason="no_action")
    store.manifest.setdefault("instruction_overrides", {})["0"] = "place the cube on the plate"
    store.manifest_path.write_text(json.dumps(store.manifest, indent=2), encoding="utf-8")
    return store


def test_minimal_rebuild_preserves_schema_videos_and_payload(minimal_dataset: tuple[Path, Path], tmp_path: Path) -> None:
    dataset_root, sidecar_base = minimal_dataset
    store = _prepare_store(dataset_root, sidecar_base)
    state = load_local_dataset(str(dataset_root))
    output_root = tmp_path / "delivery"

    result = rebuild_service.rebuild_delivery_dataset(
        state=state,
        store=store,
        output_root=output_root,
        batch_id="testbatch",
        job_id="job123",
        mode="minimal",
    )

    assert result["kept_episodes"] == 2
    assert result["removed_episodes"] == 1
    assert result["rebuild_mode"] == "minimal"

    orig_ep = pq.read_table(dataset_root / "meta/episodes/chunk-000/file-000.parquet").to_pandas()
    reb_ep = pq.read_table(output_root / "meta/episodes/chunk-000/file-000.parquet").to_pandas()
    assert list(orig_ep.columns) == list(reb_ep.columns)
    assert len(reb_ep) == 2
    assert reb_ep.iloc[0]["episode_index"] == 0
    assert reb_ep.iloc[1]["episode_index"] == 1
    assert reb_ep.iloc[0]["dataset_from_index"] == 0
    assert reb_ep.iloc[0]["dataset_to_index"] == 5
    assert reb_ep.iloc[1]["dataset_from_index"] == 5
    assert reb_ep.iloc[1]["dataset_to_index"] == 10
    assert reb_ep.iloc[0]["tasks"] == ["place the cube on the plate"]
    assert reb_ep.iloc[0]["videos/observation.images.top/from_timestamp"] == 0.0
    # Second kept row is original episode 2 (episode 1 was removed).
    assert reb_ep.iloc[1]["videos/observation.images.top/from_timestamp"] == 2.0

    orig_data = pq.read_table(dataset_root / "data/chunk-000/file-000.parquet").to_pandas()
    reb_data = pq.read_table(output_root / "data/chunk-000/file-000.parquet").to_pandas()
    assert len(reb_data) == 10
    kept_ep0 = orig_data[orig_data["episode_index"] == 0].reset_index(drop=True)
    reb_ep0 = reb_data[reb_data["episode_index"] == 0].reset_index(drop=True)
    assert np.allclose(kept_ep0["action"].tolist(), reb_ep0["action"].tolist())
    assert np.allclose(kept_ep0["observation.state"].tolist(), reb_ep0["observation.state"].tolist())
    assert np.allclose(kept_ep0["timestamp"].values, reb_ep0["timestamp"].values)
    assert reb_data["index"].tolist() == list(range(10))

    info = json.loads((output_root / "meta/info.json").read_text(encoding="utf-8"))
    assert info["total_episodes"] == 2
    assert info["total_frames"] == 10
    assert info["splits"] == {"train": "0:2"}

    assert (output_root / "videos/observation.images.top/chunk-000/file-000.mp4").read_bytes() == b"shared-rgb"
    assert (output_root / "videos/observation.images.depth/chunk-000/file-000.mkv").read_bytes() == b"shared-depth"
    assert list((output_root / "videos").rglob("*.mp4"))
    assert list((output_root / "videos").rglob("*.mkv"))
    assert not (output_root / "qc_report.txt").exists()
    assert not (output_root / "removed_episodes.txt").exists()
    report_path = store.sidecar_root / "delivery_reports" / "testbatch.txt"
    assert report_path.is_file()

    orig_tasks = (dataset_root / "meta/tasks.parquet").read_bytes()
    reb_tasks = (output_root / "meta/tasks.parquet").read_bytes()
    assert orig_tasks == reb_tasks

    stats = json.loads((output_root / "meta/stats.json").read_text(encoding="utf-8"))
    assert stats["action"]["count"] == [10]
    assert stats["timestamp"]["count"] == [10]


def test_build_episode_mapping() -> None:
    kept, mapping = rebuild_service._build_episode_mapping([0, 1, 2, 3], {1, 3})
    assert kept == [0, 2]
    assert mapping == {0: 0, 2: 1}


def test_update_splits() -> None:
    info = {"splits": {"train": "0:119", "val": "5:10"}}
    rebuild_service._update_splits(info, 115)
    assert info["splits"]["train"] == "0:115"
    assert info["splits"]["val"] == "5:115"
