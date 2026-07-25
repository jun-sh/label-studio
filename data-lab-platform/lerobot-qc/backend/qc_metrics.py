"""Episode trajectory and QC metric helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from dataset_manager import DatasetState


def _iter_data_parquet_files(root: Path) -> list[Path]:
    data_root = root / "data"
    if not data_root.is_dir():
        return []
    return sorted(data_root.rglob("*.parquet"))


def read_episode_frames(state: DatasetState, episode_index: int) -> pd.DataFrame:
    start, end = state.frame_bounds(episode_index)
    if end <= start:
        return pd.DataFrame()

    files = _iter_data_parquet_files(state.dataset_root)
    if not files:
        return pd.DataFrame()

    chunks: list[pd.DataFrame] = []
    for path in files:
        table = pq.read_table(path)
        df = table.to_pandas()
        if "episode_index" in df.columns:
            subset = df[df["episode_index"] == episode_index]
            if not subset.empty:
                chunks.append(subset)
                continue
        if "index" in df.columns:
            subset = df[(df["index"] >= start) & (df["index"] < end)]
            if not subset.empty:
                chunks.append(subset)

    if not chunks:
        # Fallback: slice by global frame index across concatenated parquet.
        collected: list[pd.DataFrame] = []
        for path in files:
            df = pd.read_parquet(path)
            if "index" not in df.columns:
                continue
            part = df[(df["index"] >= start) & (df["index"] < end)]
            if not part.empty:
                collected.append(part)
        if collected:
            return pd.concat(collected, ignore_index=True)
        return pd.DataFrame()

    merged = pd.concat(chunks, ignore_index=True)
    if "frame_index" in merged.columns:
        merged = merged.sort_values("frame_index")
    elif "index" in merged.columns:
        merged = merged.sort_values("index")
    return merged.reset_index(drop=True)


def _series_payload(values: Any) -> list[float]:
    if values is None:
        return []
    if isinstance(values, float) and np.isnan(values):
        return []
    arr = np.asarray(values, dtype=float).reshape(-1)
    return [float(x) if np.isfinite(x) else 0.0 for x in arr.tolist()]


def build_trajectory_payload(state: DatasetState, episode_index: int) -> dict[str, Any]:
    df = read_episode_frames(state, episode_index)
    if df.empty:
        return {
            "episode_index": episode_index,
            "frame_count": 0,
            "timestamps": [],
            "action": {},
            "observation_state": {},
            "scalar_features": {},
        }

    timestamps: list[float] = []
    if "timestamp" in df.columns:
        timestamps = _series_payload(df["timestamp"].tolist())
    elif "frame_index" in df.columns:
        timestamps = [float(v) / state.fps for v in df["frame_index"].tolist()]
    else:
        timestamps = [i / state.fps for i in range(len(df))]

    action_cols = [c for c in df.columns if c == "action" or c.startswith("action.")]
    state_cols = [c for c in df.columns if c == "observation.state" or c.startswith("observation.state")]

    action: dict[str, list[float]] = {}
    observation_state: dict[str, list[float]] = {}
    scalar_features: dict[str, list[float]] = {}

    preferred = state.scalar_feature_keys()
    for col in preferred:
        if col in df.columns and col not in action_cols and col not in state_cols:
            scalar_features[col] = _series_payload(df[col].tolist())

    if "action" in df.columns:
        for i, row in enumerate(df["action"].tolist()):
            vals = _series_payload(row)
            if vals:
                for j, val in enumerate(vals):
                    action.setdefault(f"dim_{j}", []).append(val)
    else:
        for col in action_cols:
            action[col] = _series_payload(df[col].tolist())

    if "observation.state" in df.columns:
        for row in df["observation.state"].tolist():
            vals = _series_payload(row)
            if vals:
                for j, val in enumerate(vals):
                    observation_state.setdefault(f"dim_{j}", []).append(val)
    else:
        for col in state_cols:
            observation_state[col] = _series_payload(df[col].tolist())

    return {
        "episode_index": episode_index,
        "frame_count": len(df),
        "timestamps": timestamps,
        "action": action,
        "observation_state": observation_state,
        "scalar_features": scalar_features,
    }


def compute_qc_metrics(state: DatasetState, episode_index: int) -> dict[str, Any]:
    row = state.episode_row(episode_index)
    length = state.episode_length(row)
    duration = length / state.fps if state.fps else 0.0
    df = read_episode_frames(state, episode_index)

    dt_max_ms = 0.0
    has_time_gap = False
    frame_rate_stable = True

    if "timestamp" in df.columns and len(df) > 1:
        ts = np.asarray(df["timestamp"].tolist(), dtype=float)
        dts = np.diff(ts)
        if len(dts):
            dt_max_ms = float(np.max(dts) * 1000.0)
            expected = 1.0 / state.fps if state.fps else 0.0
            gap_threshold = max(0.05, expected * 2.5) if expected else 0.1
            has_time_gap = bool(np.any(dts > gap_threshold))
            if expected > 0:
                frame_rate_stable = bool(np.max(np.abs(dts - expected)) < expected * 0.5)

    anomalies: list[str] = []
    if duration < 1.0:
        anomalies.append("duration_lt_1s")
    if dt_max_ms > 20.0:
        anomalies.append("dt_max_gt_20ms")
    if has_time_gap:
        anomalies.append("time_gap")

    return {
        "episode_index": episode_index,
        "duration_sec": duration,
        "frame_count": length,
        "dt_max_ms": round(dt_max_ms, 3),
        "has_time_gap": has_time_gap,
        "frame_rate_stable": frame_rate_stable,
        "anomalies": anomalies,
    }
