"""Tests for LeRobot QC dataset loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from dataset_manager import load_local_dataset, validate_v3_dataset


@pytest.fixture()
def v3_dataset(tmp_path: Path) -> Path:
    root = tmp_path / "demo_ds"
    (root / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (root / "videos" / "observation.images.top" / "chunk-000").mkdir(parents=True)
    (root / "data" / "chunk-000").mkdir(parents=True)

    (root / "meta" / "info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "robot_type": "test_robot",
                "total_episodes": 1,
                "total_frames": 10,
                "fps": 10,
                "chunks_size": 1000,
                "features": {
                    "observation.images.top": {"dtype": "video", "shape": [96, 96, 3]},
                    "action": {"dtype": "float32", "shape": [7]},
                    "observation.state": {"dtype": "float32", "shape": [7]},
                    "timestamp": {"dtype": "float32", "shape": [1]},
                },
            }
        ),
        encoding="utf-8",
    )

    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    episodes = pd.DataFrame(
        [
            {
                "episode_index": 0,
                "length": 10,
                "dataset_from_index": 0,
                "dataset_to_index": 10,
                "task_index": 0,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 1.0,
            }
        ]
    )
    pq.write_table(
        pa.Table.from_pandas(episodes, preserve_index=False),
        root / "meta" / "episodes" / "chunk-000" / "file-000.parquet",
    )
    tasks = pd.DataFrame([{"task_index": 0, "task": "pick the cube"}])
    pq.write_table(pa.Table.from_pandas(tasks, preserve_index=False), root / "meta" / "tasks.parquet")

    frames = pd.DataFrame(
        {
            "episode_index": [0] * 10,
            "frame_index": list(range(10)),
            "index": list(range(10)),
            "timestamp": [i / 10 for i in range(10)],
            "action": [[0.0] * 7 for _ in range(10)],
            "observation.state": [[0.1] * 7 for _ in range(10)],
        }
    )
    pq.write_table(pa.Table.from_pandas(frames, preserve_index=False), root / "data" / "chunk-000" / "file-000.parquet")
    (root / "videos" / "observation.images.top" / "chunk-000" / "file-000.mp4").write_bytes(b"")
    return root


def test_reject_non_v3(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    (root / "meta").mkdir(parents=True)
    (root / "meta" / "info.json").write_text(json.dumps({"codebase_version": "v2.1"}), encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        validate_v3_dataset(root)
    assert exc.value.status_code == 400


def test_load_v3_dataset(v3_dataset: Path) -> None:
    state = load_local_dataset(str(v3_dataset))
    assert state.info["codebase_version"] == "v3.0"
    summary = state.build_summary()
    assert summary["episodes"][0]["language_instruction"] == "pick the cube"
