"""Tests for lightweight dataset load and paginated episode listing."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

import app as qc_app
from dataset_manager import load_local_dataset
from qc_store import QcStore


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
                "total_episodes": 3,
                "total_frames": 30,
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

    episodes = pd.DataFrame(
        [
            {
                "episode_index": idx,
                "length": 10,
                "dataset_from_index": idx * 10,
                "dataset_to_index": (idx + 1) * 10,
                "task_index": 0,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 1.0,
            }
            for idx in range(3)
        ]
    )
    pq.write_table(
        pa.Table.from_pandas(episodes, preserve_index=False),
        root / "meta" / "episodes" / "chunk-000" / "file-000.parquet",
    )
    tasks = pd.DataFrame([{"task_index": 0, "task": "pick the cube"}])
    pq.write_table(pa.Table.from_pandas(tasks, preserve_index=False), root / "meta" / "tasks.parquet")
    (root / "videos" / "observation.images.top" / "chunk-000" / "file-000.mp4").write_bytes(b"")
    return root


@pytest.fixture(autouse=True)
def clear_sessions() -> None:
    qc_app.sessions.clear()


def test_build_summary_can_omit_episodes(v3_dataset: Path) -> None:
    state = load_local_dataset(str(v3_dataset))
    summary = state.build_summary(include_episodes=False)
    assert summary["total_episodes"] == 3
    assert summary["episodes"] == []


def test_review_summary_uses_total_count(v3_dataset: Path) -> None:
    store = QcStore.open(v3_dataset, v3_dataset.parent / "sidecar", operator_id="tester")
    store.set_review(0, "approved")
    store.set_review(1, "rejected", reason="bad")
    summary = store.review_summary(total_episodes=3)
    assert summary["total"] == 3
    assert summary["approved"] == 1
    assert summary["rejected"] == 1
    assert summary["pending"] == 1


def test_load_and_paginate_episodes(v3_dataset: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qc_app, "validate_dataset_path", lambda path: Path(path).resolve())
    client = TestClient(qc_app.app)

    load_res = client.post(
        "/api/dataset/load",
        json={"local_path": str(v3_dataset), "operator_id": "reviewer-a"},
    )
    assert load_res.status_code == 200
    payload = load_res.json()
    assert payload["total_episodes"] == 3
    assert payload["episodes"] == []
    assert payload["qc_state"] == {}

    page_res = client.get(
        "/api/episodes",
        params={"offset": 1, "limit": 1},
        headers={"X-Dataset-Path": str(v3_dataset)},
    )
    assert page_res.status_code == 200
    page = page_res.json()
    assert page["total"] == 3
    assert page["has_more"] is True
    assert len(page["items"]) == 1
    assert page["items"][0]["episode_index"] == 1


def test_episode_status_filter(v3_dataset: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = QcStore.open(v3_dataset, v3_dataset.parent / "sidecar", operator_id="tester")
    store.set_review(2, "suspicious")
    state = load_local_dataset(str(v3_dataset))
    qc_app.sessions.open(state=state, store=store, local_path=str(v3_dataset))

    page = qc_app._list_episodes_page(state, store, offset=0, limit=10, status_filter="suspicious")
    assert page["total_matching"] == 1
    assert page["items"][0]["episode_index"] == 2
