"""Tests for coarse screening import."""

from __future__ import annotations

from pathlib import Path

import pytest

from qc_store import QcStore
from screening_service import import_screening_payload


@pytest.fixture()
def store(tmp_path: Path) -> QcStore:
    dataset_root = tmp_path / "source_ds"
    dataset_root.mkdir()
    return QcStore.open(dataset_root, tmp_path / "sidecar", operator_id="tester")


def test_import_screening_marks_suspicious_and_rejected(store: QcStore) -> None:
    result = import_screening_payload(
        store,
        {
            "batch_id": "batch-001",
            "suspicious_episodes": [{"episode_index": 3, "reason": "dt_max_gt_20ms"}],
            "auto_reject_episodes": [{"episode_index": 9, "reason": "black_screen"}],
        },
    )
    assert result["suspicious"] == [3]
    assert result["rejected"] == [9]
    assert store.get_review(3)["status"] == "suspicious"
    assert store.get_review(9)["status"] == "rejected"
    assert 9 in store.removed_episode_indices()
