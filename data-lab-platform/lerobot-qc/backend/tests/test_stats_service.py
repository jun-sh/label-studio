"""Tests for delivery stats recomputation."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

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


def _random_episode_stats(rng: np.random.Generator, shape: tuple[int, ...], count: int) -> dict[str, np.ndarray]:
    stats = {
        "min": rng.normal(size=shape) - 3,
        "max": rng.normal(size=shape) + 3,
        "mean": rng.normal(size=shape),
        "std": np.abs(rng.normal(size=shape)),
        "count": np.array([count]),
    }
    for key in ("q01", "q10", "q50", "q90", "q99"):
        stats[key] = rng.normal(size=shape)
    return stats


def test_aggregate_feature_stats_matches_lerobot() -> None:
    lerobot_stats = pytest.importorskip("lerobot.datasets.compute_stats")
    from stats_service import aggregate_feature_stats

    rng = np.random.default_rng(0)
    for shape in ((7,), (3, 1, 1), (1,)):
        stats_list = [_random_episode_stats(rng, shape, int(rng.integers(5, 200))) for _ in range(6)]
        ours = aggregate_feature_stats(stats_list)
        theirs = lerobot_stats.aggregate_feature_stats(stats_list)
        assert set(ours) == set(theirs)
        for key in theirs:
            np.testing.assert_allclose(ours[key], theirs[key], rtol=1e-12, err_msg=f"{shape} {key}")


def test_get_feature_stats_matches_lerobot_tabular_layout() -> None:
    lerobot_stats = pytest.importorskip("lerobot.datasets.compute_stats")
    from stats_service import get_feature_stats

    rng = np.random.default_rng(1)
    for values in (rng.normal(size=(500, 7)), rng.integers(0, 4, size=500).astype(np.int64)):
        ours = get_feature_stats(values)
        theirs = lerobot_stats.get_feature_stats(
            values, axis=0, keepdims=values.ndim == 1, quantile_list=lerobot_stats.DEFAULT_QUANTILES
        )
        assert set(ours) == set(theirs)
        for key in ("min", "max", "mean", "std", "count"):
            assert np.shape(ours[key]) == np.shape(theirs[key]), key
            np.testing.assert_allclose(ours[key], theirs[key], rtol=1e-6, atol=1e-9, err_msg=key)
        # lerobot approximates quantiles with a histogram; ours are exact.
        spread = np.asarray(theirs["max"]) - np.asarray(theirs["min"])
        for key in ("q01", "q10", "q50", "q90", "q99"):
            assert np.shape(ours[key]) == np.shape(theirs[key]), key
            assert np.all(np.abs(ours[key] - theirs[key]) <= 0.05 * spread + 1e-9), key


def test_get_feature_stats_keeps_multidim_feature_shape() -> None:
    from stats_service import get_feature_stats

    stats = get_feature_stats(np.ones((10, 2, 3)))
    assert stats["mean"].shape == (2, 3)
    assert stats["q50"].shape == (2, 3)
    assert stats["count"].tolist() == [10]


def test_recompute_dataset_stats_without_lerobot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The QC container ships without lerobot; recompute must not import it."""
    import sys

    for name in [m for m in sys.modules if m == "lerobot" or m.startswith("lerobot.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "lerobot", None)

    root = tmp_path / "dataset"
    (root / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (root / "data" / "chunk-000").mkdir(parents=True)
    rng = np.random.default_rng(2)
    episodes = pd.DataFrame(
        [
            {
                "episode_index": ep,
                **{f"stats/action/{k}": v for k, v in _random_episode_stats(rng, (2,), 5).items()},
            }
            for ep in range(2)
        ]
    )
    pq.write_table(
        pa.Table.from_pandas(episodes, preserve_index=False),
        root / "meta" / "episodes" / "chunk-000" / "file-000.parquet",
    )
    frames = pd.DataFrame(
        {
            "episode_index": [0] * 5 + [1] * 5,
            "frame_index": list(range(5)) * 2,
            "index": list(range(10)),
            "task_index": [0] * 5 + [3] * 5,
            "action": [[0.0, 1.0]] * 10,
        }
    )
    pq.write_table(pa.Table.from_pandas(frames, preserve_index=False), root / "data" / "chunk-000" / "file-000.parquet")
    info = {
        "features": {
            "action": {"dtype": "float32", "shape": [2]},
            "episode_index": {"dtype": "int64", "shape": [1]},
            "frame_index": {"dtype": "int64", "shape": [1]},
            "index": {"dtype": "int64", "shape": [1]},
            "task_index": {"dtype": "int64", "shape": [1]},
        }
    }

    assert recompute_dataset_stats(root, info) is True
    stats = json.loads((root / "meta" / "stats.json").read_text(encoding="utf-8"))
    assert stats["action"]["count"] == [10]
    assert stats["task_index"]["max"] == [3.0]
    assert stats["task_index"]["mean"] == [1.5]
