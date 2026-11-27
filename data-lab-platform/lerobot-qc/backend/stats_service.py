"""Recompute LeRobot-compatible global stats.json for delivery datasets."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# Frame-index metadata: per-episode stats in episodes parquet are not patched during
# minimal rebuild, so global stats for these keys must be derived from data/.
INDEX_LIKE_FEATURES = frozenset(
    {
        "episode_index",
        "frame_index",
        "index",
        "timestamp",
        "task_index",
    }
)


def _visual_stat_shape(feature_info: dict[str, Any]) -> tuple[int, int, int]:
    shape = feature_info.get("shape") or []
    if str(feature_info.get("dtype") or "") in ("image", "video") and shape:
        channels = int(shape[0])
        return (channels, 1, 1)
    return (3, 1, 1)


def _normalize_image_stat_value(
    value: Any,
    feature_info: dict[str, Any],
    stat_name: str,
) -> np.ndarray:
    feature_dtype = str(feature_info.get("dtype") or "")
    if stat_name == "count":
        arr = np.asarray(value)
        return arr.reshape(1) if arr.ndim == 0 else arr

    if feature_dtype not in ("image", "video"):
        return np.asarray(value)

    target_shape = _visual_stat_shape(feature_info)
    channels = target_shape[0]

    if isinstance(value, np.ndarray) and value.dtype == object:
        flat_values: list[float] = []
        for item in value:
            while isinstance(item, np.ndarray):
                item = item.flatten()[0]
            flat_values.append(float(item))
        flat = np.array(flat_values, dtype=np.float64)
        if flat.size == channels:
            return flat.reshape(target_shape)
        if flat.size == 1 and channels == 1:
            return flat.reshape(1, 1, 1)
        raise ValueError(
            f"Unexpected visual stat size {flat.size} for {channels} channels ({stat_name})"
        )

    arr = np.asarray(value, dtype=np.float64)
    if arr.shape == (channels,):
        return arr.reshape(target_shape)
    if arr.shape == target_shape:
        return arr
    if arr.size == channels:
        return arr.reshape(target_shape)
    return arr


def extract_episode_stats_from_row(row: pd.Series, features: dict[str, Any]) -> dict[str, dict[str, np.ndarray]]:
    episode_stats: dict[str, dict[str, np.ndarray]] = {}
    for column, value in row.items():
        if not isinstance(column, str) or not column.startswith("stats/"):
            continue
        if value is None or (isinstance(value, float) and np.isnan(value)):
            continue
        if isinstance(value, np.ndarray) and pd.isna(value).all():
            continue
        stat_key = column.removeprefix("stats/")
        feature_name, stat_name = stat_key.split("/", 1)
        feature_info = features.get(feature_name) or {}
        episode_stats.setdefault(feature_name, {})[stat_name] = _normalize_image_stat_value(
            value,
            feature_info,
            stat_name,
        )
    return episode_stats


def _is_usable_episode_stats(episode_stats: dict[str, dict[str, np.ndarray]]) -> bool:
    if not episode_stats:
        return False
    required = {"min", "max", "mean", "std", "count"}
    for feature_stats in episode_stats.values():
        if not required.issubset(feature_stats):
            return False
    return True


# Same quantiles LeRobot writes into stats.json (lerobot.datasets.compute_stats.DEFAULT_QUANTILES).
DEFAULT_QUANTILES = (0.01, 0.1, 0.5, 0.9, 0.99)


def _quantile_key(q: float) -> str:
    return f"q{int(round(q * 100)):02d}"


def aggregate_feature_stats(stats_ft_list: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    """Merge per-episode stats of one feature.

    Port of lerobot 0.4.4 ``aggregate_feature_stats`` (count-weighted mean, parallel-variance
    std, count-weighted quantiles) so the QC image does not need the lerobot/torch stack.
    """
    means = np.stack([s["mean"] for s in stats_ft_list])
    variances = np.stack([s["std"] ** 2 for s in stats_ft_list])
    counts = np.stack([s["count"] for s in stats_ft_list])
    total_count = counts.sum(axis=0)

    while counts.ndim < means.ndim:
        counts = np.expand_dims(counts, axis=-1)

    total_mean = (means * counts).sum(axis=0) / total_count
    delta_means = means - total_mean
    total_variance = ((variances + delta_means**2) * counts).sum(axis=0) / total_count

    aggregated: dict[str, np.ndarray] = {
        "min": np.min(np.stack([s["min"] for s in stats_ft_list]), axis=0),
        "max": np.max(np.stack([s["max"] for s in stats_ft_list]), axis=0),
        "mean": total_mean,
        "std": np.sqrt(total_variance),
        "count": total_count,
    }
    if stats_ft_list:
        quantile_keys = [k for k in stats_ft_list[0] if k.startswith("q") and k[1:].isdigit()]
        for q_key in quantile_keys:
            if all(q_key in s for s in stats_ft_list):
                quantile_values = np.stack([s[q_key] for s in stats_ft_list])
                aggregated[q_key] = (quantile_values * counts).sum(axis=0) / total_count
    return aggregated


def get_feature_stats(values: np.ndarray) -> dict[str, np.ndarray]:
    """Exact per-feature stats over frames (axis 0), shaped like lerobot's tabular stats.

    1-D inputs keep a trailing dimension of size 1; quantiles are exact (lerobot uses a
    histogram approximation, so q* may differ slightly from a lerobot recompute).
    """
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 1:
        array = array[:, None]
    feature_shape = array.shape[1:]
    array = array.reshape(array.shape[0], -1)
    stats: dict[str, np.ndarray] = {
        "min": array.min(axis=0),
        "max": array.max(axis=0),
        "mean": array.mean(axis=0),
        "std": array.std(axis=0),
        "count": np.array([array.shape[0]]),
    }
    for q, value in zip(DEFAULT_QUANTILES, np.quantile(array, DEFAULT_QUANTILES, axis=0)):
        stats[_quantile_key(q)] = value
    return {
        key: value if key == "count" else value.reshape(feature_shape)
        for key, value in stats.items()
    }


def _aggregate_episode_stats(episode_stats_list: list[dict[str, dict[str, np.ndarray]]]) -> dict[str, Any]:
    data_keys = {key for stats in episode_stats_list for key in stats}
    aggregated: dict[str, Any] = {}
    for key in sorted(data_keys):
        stats_with_key = [stats[key] for stats in episode_stats_list if key in stats]
        aggregated[key] = aggregate_feature_stats(stats_with_key)
    return aggregated


def _compute_tabular_stats_from_data(
    output_root: Path,
    features: dict[str, Any],
    *,
    feature_names: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    frames: list[pd.DataFrame] = []
    for parquet_path in sorted((output_root / "data").rglob("*.parquet")):
        if "sensor_raw" in parquet_path.parts:
            continue
        frames.append(pq.read_table(parquet_path).to_pandas())
    if not frames:
        return {}

    data_df = pd.concat(frames, ignore_index=True)
    stats: dict[str, Any] = {}
    for feature_name, feature_info in features.items():
        if feature_names is not None and feature_name not in feature_names:
            continue
        dtype = str(feature_info.get("dtype") or "")
        if dtype in ("video", "image", "string") or feature_name not in data_df.columns:
            continue
        values = np.stack(data_df[feature_name].to_numpy())
        if values.shape[0] == 0:
            continue
        stats[feature_name] = get_feature_stats(values)
    return stats


def _apply_index_stats_from_data(
    aggregated: dict[str, Any],
    output_root: Path,
    features: dict[str, Any],
) -> dict[str, Any]:
    index_stats = _compute_tabular_stats_from_data(
        output_root,
        features,
        feature_names=INDEX_LIKE_FEATURES,
    )
    if not index_stats:
        return aggregated
    merged = dict(aggregated)
    merged.update(index_stats)
    return merged


def _write_stats_payload(output_root: Path, stats: dict[str, Any]) -> None:
    """Write meta/stats.json the way lerobot.datasets.utils.write_stats does (indent=4)."""

    def _serialize(value: Any) -> Any:
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {key: _serialize(item) for key, item in value.items()}
        return value

    stats_path = output_root / "meta" / "stats.json"
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(
        json.dumps(_serialize(stats), indent=4, ensure_ascii=False),
        encoding="utf-8",
    )


def recompute_dataset_stats(output_root: Path, info: dict[str, Any]) -> bool:
    """Aggregate per-episode stats into meta/stats.json (LeRobot v3 format)."""
    features = info.get("features") or {}
    episode_frames: list[pd.DataFrame] = []
    for parquet_path in sorted((output_root / "meta" / "episodes").rglob("*.parquet")):
        episode_frames.append(pd.read_parquet(parquet_path))

    if not episode_frames:
        logger.warning("No episodes parquet found under %s", output_root)
        return False

    episodes_df = pd.concat(episode_frames, ignore_index=True)
    episode_stats_list = [
        extract_episode_stats_from_row(row, features) for _, row in episodes_df.iterrows()
    ]
    usable_episode_stats = [stats for stats in episode_stats_list if _is_usable_episode_stats(stats)]

    if len(usable_episode_stats) == len(episodes_df) and usable_episode_stats:
        aggregated = _aggregate_episode_stats(usable_episode_stats)
        aggregated = _apply_index_stats_from_data(aggregated, output_root, features)
    else:
        logger.info(
            "Episode stats incomplete (%s/%s usable); computing tabular stats from data/",
            len(usable_episode_stats),
            len(episodes_df),
        )
        aggregated = _compute_tabular_stats_from_data(output_root, features)

    if not aggregated:
        logger.warning("No statistics computed for %s", output_root)
        return False

    filtered = {key: stats for key, stats in aggregated.items() if key in features}
    _write_stats_payload(output_root, filtered)
    return True
