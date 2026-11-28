"""Exact duplicate episode detection (fingerprint of state + action in frame order)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from episode_fingerprint import duplicate_groups, episode_fingerprints, find_duplicate_episodes
from test_minimal_rebuild import minimal_dataset  # noqa: F401  (fixture)


def _write(path: Path, rows: list[tuple[int, int, list[float], list[float]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=["episode_index", "frame_index", "observation.state", "action"])
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def _episode(ep: int, base: float, n: int = 4) -> list[tuple[int, int, list[float], list[float]]]:
    return [(ep, i, [base + i, 1.0], [base - i]) for i in range(n)]


def test_identical_episodes_are_grouped(tmp_path: Path) -> None:
    rows = _episode(0, 1.0) + _episode(1, 5.0) + _episode(2, 1.0) + _episode(3, 9.0) + _episode(4, 5.0)
    _write(tmp_path / "data/chunk-000/file-000.parquet", rows)
    assert find_duplicate_episodes(tmp_path) == [[0, 2], [1, 4]]


def test_fingerprint_ignores_row_order_and_file_split(tmp_path: Path) -> None:
    a = tmp_path / "a"
    _write(a / "data/chunk-000/file-000.parquet", _episode(0, 1.0) + _episode(1, 1.0))
    b = tmp_path / "b"
    ep0 = _episode(0, 1.0)
    _write(b / "data/chunk-000/file-000.parquet", list(reversed(ep0[:2])))
    _write(b / "data/chunk-000/file-001.parquet", ep0[2:])
    assert episode_fingerprints(a)[0] == episode_fingerprints(b)[0]
    assert find_duplicate_episodes(a) == [[0, 1]]


def test_one_differing_frame_is_not_a_duplicate(tmp_path: Path) -> None:
    other = _episode(1, 1.0)
    other[2] = (1, 2, [3.0, 1.0], [-1.0 + 1e-3])
    _write(tmp_path / "data/chunk-000/file-000.parquet", _episode(0, 1.0) + other)
    assert find_duplicate_episodes(tmp_path) == []


def test_no_state_or_action_columns_means_no_fingerprints(tmp_path: Path) -> None:
    path = tmp_path / "data/chunk-000/file-000.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.table({"episode_index": [0, 1], "frame_index": [0, 0]}), path)
    assert episode_fingerprints(tmp_path) == {}


def test_duplicate_groups_order() -> None:
    assert duplicate_groups({5: "x", 1: "y", 3: "x", 0: "z", 2: "y"}) == [[1, 2], [3, 5]]


def test_coarse_screen_auto_rejects_later_copies(minimal_dataset) -> None:  # noqa: F811
    from tools.coarse_screen import build_screening_report

    root, _ = minimal_dataset
    path = root / "data/chunk-000/file-000.parquet"
    data = pd.read_parquet(path)
    ep0 = data[data["episode_index"] == 0]
    mask = data["episode_index"] == 2
    data.loc[mask, "action"] = pd.Series(ep0["action"].tolist(), index=data.index[mask])
    data.loc[mask, "observation.state"] = pd.Series(ep0["observation.state"].tolist(), index=data.index[mask])
    pq.write_table(pa.Table.from_pandas(data, preserve_index=False), path)

    report = build_screening_report(root, sample_rate=0.0)
    assert report["duplicate_groups"] == [[0, 2]]
    assert report["summary"]["duplicate_group_count"] == 1
    rejected = {x["episode_index"]: x for x in report["auto_reject_episodes"]}
    assert "duplicate_of_0" in rejected[2]["reason"]
    assert "duplicate_episode" in rejected[2]["flags"]
    assert 0 not in rejected
    assert all(x["episode_index"] != 2 for x in report["suspicious_episodes"])
