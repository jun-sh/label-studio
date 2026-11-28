"""Detect exact duplicate episodes in a LeRobot v3 dataset.

An episode's fingerprint is the MD5 of its observation.state and action values in frame order.
Two episodes with the same fingerprint carry the same recording; upstream conversions have
been seen to export one recording twice (identical video too), which inflates episode counts
and leaks across train/val splits.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

FINGERPRINT_COLUMNS = ("observation.state", "action")


def episode_fingerprints(root: Path) -> dict[int, str]:
    """Return {episode_index: hex digest}; empty when the data has no state/action columns.

    Data files are read one at a time in chunk/file order (frames of an episode never go
    backwards across files), so memory stays bounded by one data file.
    """
    files = sorted((Path(root) / "data").rglob("*.parquet"))
    # One running digest per (episode, column), so the result does not depend on where an
    # episode is split across data files.
    hashes: dict[int, dict[str, "hashlib._Hash"]] = {}
    for path in files:
        names = set(pq.read_schema(path).names)
        columns = [c for c in FINGERPRINT_COLUMNS if c in names]
        if not columns:
            return {}
        df = pq.read_table(path, columns=["episode_index", "frame_index", *columns]).to_pandas()
        df = df.sort_values(["episode_index", "frame_index"], kind="stable")
        for episode, group in df.groupby("episode_index", sort=False):
            per_column = hashes.setdefault(int(episode), {})
            for column in columns:
                values = np.stack([np.asarray(v, dtype=np.float32).reshape(-1) for v in group[column]])
                per_column.setdefault(column, hashlib.md5()).update(np.ascontiguousarray(values).tobytes())
    return {
        episode: hashlib.md5(
            "|".join(f"{col}:{per_column[col].hexdigest()}" for col in sorted(per_column)).encode()
        ).hexdigest()
        for episode, per_column in hashes.items()
    }


def duplicate_groups(fingerprints: dict[int, str]) -> list[list[int]]:
    """Groups of episode indices sharing a fingerprint, each sorted, ordered by first member."""
    by_hash: dict[str, list[int]] = {}
    for episode, digest in fingerprints.items():
        by_hash.setdefault(digest, []).append(episode)
    return sorted((sorted(group) for group in by_hash.values() if len(group) > 1), key=lambda g: g[0])


def find_duplicate_episodes(root: Path) -> list[list[int]]:
    return duplicate_groups(episode_fingerprints(root))
