"""Tests for delivery stats recomputation."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stats_service import (
    _normalize_image_stat_value,
    extract_episode_stats_from_row,
    recompute_dataset_stats,
)


def test_extract_episode_stats_from_row() -> None:
    row = pd.Series(
        {
            "stats/action/mean": np.array([0.1, 0.2]),
            "stats/action/count": np.array([5]),
            "episode_index": 0,
        }
    )
    features = {"action": {"dtype": "float32", "shape": [2]}}
    stats = extract_episode_stats_from_row(row, features)
    assert list(stats["action"]["mean"]) == [0.1, 0.2]
    assert stats["action"]["count"].tolist() == [5]


def test_recompute_dataset_stats_from_data(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    (root / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (root / "data" / "chunk-000").mkdir(parents=True)

    episodes = pd.DataFrame(
        [
            {"episode_index": 0, "length": 2},
            {"episode_index": 1, "length": 2},
        ]
    )
    pq.write_table(pa.Table.from_pandas(episodes, preserve_index=False), root / "meta/episodes/chunk-000/file-000.parquet")

    data = pd.DataFrame(
        {
            "episode_index": [0, 0, 1, 1],
            "action": [[0.0, 1.0], [2.0, 3.0], [4.0, 5.0], [6.0, 7.0]],
        }
    )
    pq.write_table(pa.Table.from_pandas(data, preserve_index=False), root / "data/chunk-000/file-000.parquet")

    info = {"features": {"action": {"dtype": "float32", "shape": [2]}}}
    assert recompute_dataset_stats(root, info) is True

    stats = json.loads((root / "meta/stats.json").read_text(encoding="utf-8"))
    assert stats["action"]["count"] == [4]
    assert stats["action"]["min"] == [0.0, 1.0]
    assert stats["action"]["max"] == [6.0, 7.0]


def test_normalize_depth_video_stat_shape() -> None:
    depth_info = {"dtype": "video", "shape": [1, 720, 1280]}
    value = np.array([np.array([np.array([1147.9])], dtype=object)], dtype=object)
    normalized = _normalize_image_stat_value(value, depth_info, "mean")
    assert normalized.shape == (1, 1, 1)


def test_normalize_rgb_video_stat_shape() -> None:
    rgb_info = {"dtype": "video", "shape": [3, 720, 1280]}
    value = np.array(
        [
            np.array([np.array([0.72])], dtype=object),
            np.array([np.array([0.71])], dtype=object),
            np.array([np.array([0.70])], dtype=object),
        ],
        dtype=object,
    )
    normalized = _normalize_image_stat_value(value, rgb_info, "mean")
    assert normalized.shape == (3, 1, 1)


def test_recompute_overrides_stale_index_stats_from_data(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    (root / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (root / "data" / "chunk-000").mkdir(parents=True)

    episodes = pd.DataFrame(
        [
            {
                "episode_index": 0,
                "length": 2,
                "stats/episode_index/max": np.array([99.0]),
                "stats/episode_index/min": np.array([99.0]),
                "stats/episode_index/mean": np.array([99.0]),
                "stats/episode_index/std": np.array([0.0]),
                "stats/episode_index/count": np.array([2]),
                "stats/index/max": np.array([999.0]),
                "stats/index/min": np.array([999.0]),
                "stats/index/mean": np.array([999.0]),
                "stats/index/std": np.array([0.0]),
                "stats/index/count": np.array([2]),
            },
            {
                "episode_index": 1,
                "length": 2,
                "stats/episode_index/max": np.array([99.0]),
                "stats/episode_index/min": np.array([99.0]),
                "stats/episode_index/mean": np.array([99.0]),
                "stats/episode_index/std": np.array([0.0]),
                "stats/episode_index/count": np.array([2]),
                "stats/index/max": np.array([999.0]),
                "stats/index/min": np.array([999.0]),
                "stats/index/mean": np.array([999.0]),
                "stats/index/std": np.array([0.0]),
                "stats/index/count": np.array([2]),
            },
        ]
    )
    pq.write_table(pa.Table.from_pandas(episodes, preserve_index=False), root / "meta/episodes/chunk-000/file-000.parquet")

    data = pd.DataFrame(
        {
            "episode_index": [0, 0, 1, 1],
            "index": [0, 1, 2, 3],
            "frame_index": [0, 1, 0, 1],
            "timestamp": [0.0, 0.2, 0.0, 0.2],
            "task_index": [0, 0, 1, 1],
            "action": [[0.0, 1.0], [2.0, 3.0], [4.0, 5.0], [6.0, 7.0]],
        }
    )
    pq.write_table(pa.Table.from_pandas(data, preserve_index=False), root / "data/chunk-000/file-000.parquet")

    info = {
        "features": {
            "episode_index": {"dtype": "int64", "shape": [1]},
            "index": {"dtype": "int64", "shape": [1]},
            "frame_index": {"dtype": "int64", "shape": [1]},
            "timestamp": {"dtype": "float32", "shape": [1]},
            "task_index": {"dtype": "int64", "shape": [1]},
            "action": {"dtype": "float32", "shape": [2]},
        }
    }
    assert recompute_dataset_stats(root, info) is True

    stats = json.loads((root / "meta/stats.json").read_text(encoding="utf-8"))
    assert stats["episode_index"]["max"] == [1]
    assert stats["index"]["max"] == [3]
    assert stats["frame_index"]["max"] == [1]
