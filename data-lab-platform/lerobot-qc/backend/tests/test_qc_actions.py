"""Tests for QC review and instruction API validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import app as qc_app
from dataset_sessions import DatasetSession
from qc_store import QcStore


@pytest.fixture()
def loaded_qc(tmp_path: Path) -> tuple[MagicMock, QcStore, DatasetSession]:
    dataset_root = tmp_path / "source"
    dataset_root.mkdir()
    sidecar = tmp_path / "sidecar"

    state = MagicMock()
    state.dataset_root = dataset_root
    state.episodes_df = __import__("pandas").DataFrame({"episode_index": [0, 1, 2]})
    state.episode_row = MagicMock(return_value=MagicMock())
    state.resolve_language_instruction = MagicMock(return_value=("original task", 0))

    store = QcStore.open(dataset_root, sidecar, operator_id="tester")
    qc_app.sessions.clear()
    session = qc_app.sessions.open(state=state, store=store, local_path=str(dataset_root))
    return state, store, session


def test_reject_requires_reason(loaded_qc: tuple[MagicMock, QcStore, DatasetSession]) -> None:
    _, _, session = loaded_qc
    with pytest.raises(HTTPException) as exc:
        qc_app.api_qc_review(qc_app.ReviewRequest(episode_index=0, status="rejected"), session=session)
    assert exc.value.status_code == 400


def test_instruction_rejects_empty_text(loaded_qc: tuple[MagicMock, QcStore, DatasetSession]) -> None:
    _, _, session = loaded_qc
    with pytest.raises(HTTPException) as exc:
        qc_app.api_qc_instruction(qc_app.InstructionRequest(episode_index=0, instruction="   "), session=session)
    assert exc.value.status_code == 400


def test_instruction_rejects_removed_episode(loaded_qc: tuple[MagicMock, QcStore, DatasetSession]) -> None:
    _, store, session = loaded_qc
    store.set_review(0, "rejected", reason="bad_quality")
    with pytest.raises(HTTPException) as exc:
        qc_app.api_qc_instruction(qc_app.InstructionRequest(episode_index=0, instruction="new text"), session=session)
    assert exc.value.status_code == 400


def test_removed_episode_detail_allows_preview(loaded_qc: tuple[MagicMock, QcStore, DatasetSession]) -> None:
    state, store, session = loaded_qc
    state.fps = 5.0
    state.episode_length = MagicMock(return_value=10)
    state.resolve_language_instruction = MagicMock(return_value=("task text", 0))
    state.video_keys = MagicMock(return_value=["observation.images.exterior_image_1"])
    state.video_key = "observation.images.exterior_image_1"
    store.set_review(1, "rejected", reason="no_action")
    with patch.object(qc_app, "compute_qc_metrics", return_value={"duration_sec": 2.0}):
        response = qc_app.api_episode_detail(1, session=session)
    payload = __import__("json").loads(response.body)
    assert payload["is_removed"] is True
    assert payload["removal_reason"] == "no_action"


def test_restore_removed_episode_via_pending(loaded_qc: tuple[MagicMock, QcStore, DatasetSession]) -> None:
    _, store, session = loaded_qc
    store.set_review(2, "rejected", reason="bad_quality")
    response = qc_app.api_qc_review(qc_app.ReviewRequest(episode_index=2, status="pending"), session=session)
    payload = __import__("json").loads(response.body)
    assert payload["review"]["status"] == "pending"
    assert 2 not in store.removed_episode_indices()


def test_instruction_keeps_suspicious_status(loaded_qc: tuple[MagicMock, QcStore, DatasetSession]) -> None:
    _, store, session = loaded_qc
    store.set_review(1, "suspicious", note="flagged")
    response = qc_app.api_qc_instruction(qc_app.InstructionRequest(episode_index=1, instruction="corrected task"), session=session)
    payload = __import__("json").loads(response.body)
    assert payload["instruction"] == "corrected task"
    assert store.get_review(1)["status"] == "suspicious"


def test_instruction_auto_approves_pending(loaded_qc: tuple[MagicMock, QcStore, DatasetSession]) -> None:
    _, _, session = loaded_qc
    response = qc_app.api_qc_instruction(qc_app.InstructionRequest(episode_index=2, instruction="corrected task"), session=session)
    payload = __import__("json").loads(response.body)
    assert payload["review"]["status"] == "approved"


def test_review_uses_request_operator_id(loaded_qc: tuple[MagicMock, QcStore, DatasetSession]) -> None:
    _, store, session = loaded_qc
    assert store.operator_id == "tester"
    response = qc_app.api_qc_review(
        qc_app.ReviewRequest(episode_index=0, status="approved", operator_id="reviewer-02"),
        session=session,
    )
    payload = __import__("json").loads(response.body)
    assert payload["review"]["operator_id"] == "reviewer-02"
    assert store.get_review(0)["operator_id"] == "reviewer-02"


def test_instruction_uses_request_operator_id(loaded_qc: tuple[MagicMock, QcStore, DatasetSession]) -> None:
    _, store, session = loaded_qc
    response = qc_app.api_qc_instruction(
        qc_app.InstructionRequest(episode_index=1, instruction="corrected task", operator_id="reviewer-03"),
        session=session,
    )
    payload = __import__("json").loads(response.body)
    assert payload["review"]["operator_id"] == "reviewer-03"
    assert store.get_review(1)["operator_id"] == "reviewer-03"
